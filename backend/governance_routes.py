"""AI governance: the model catalog and its benchmark status, benchmark runs, per-project calibration before AI
suggestions are trusted, bias monitoring, reproducibility checks, and the quality reports.

Administrators manage the catalog and benchmarks (auth_routes.require_admin, granted with scripts/make_admin.py).
Projects run their own calibration against records their reviewers have already decided.
"""

import asyncio
import logging
import random
from collections import Counter
from typing import Any, Literal

from fastapi import APIRouter, Depends, File, HTTPException, Query, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import benchmarks
import models
from agreement import wilson_interval
from ai_access import project_ai
from ai_catalog import ai_model_out
from ai_disclosure import ai_use
from audit import record_event
from auth_routes import require_admin
from database import get_db
from llm.prompts import SCREENING_PROMPT
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from review_data import FULL_TEXT, TITLE_ABSTRACT
from review_settings import load_settings
from services.ai_screening import evaluate_eligibility
from services.errors import LLMError
from workflow import STAGE_LABELS, STAGES

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["governance"])
admin_router = APIRouter(prefix="/api/admin", tags=["administration"], dependencies=[Depends(require_admin)])

MAX_CALIBRATION_SAMPLE = 100
MIN_CALIBRATION_SAMPLE = 10
CALIBRATION_CHUNK = 5


# --- Administration: the model catalog ---


class ModelUpdate(BaseModel):
    enabled: bool | None = None
    is_default: bool | None = None
    benchmark_status: Literal["unvalidated", "passed", "failed", "exempt"] | None = None
    status_note: str | None = Field(None, max_length=2000)


def admin_model_out(db: Session, model: models.AIModel) -> dict:
    runs = benchmarks.latest_runs(db, model) if model.purpose == "chat" else {}
    return {
        **ai_model_out(model),
        "benchmark_status": model.benchmark_status,
        "validated_prompts": model.validated_prompts,
        "status_note": model.status_note,
        "benchmarks": {
            task: (
                {
                    "run_id": run.id,
                    "dataset": run.dataset_name,
                    "passed": run.passed,
                    "metrics": run.metrics,
                    "prompt_version": run.prompt_version,
                    "finished_at": run.finished_at,
                }
                if run is not None
                else None
            )
            for task, run in runs.items()
        },
        "current_prompt_versions": {task: benchmarks.prompt_version(task) for task in benchmarks.MODEL_TASKS},
    }


@admin_router.get("/models")
def admin_list_models(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.AIModel).order_by(models.AIModel.provider, models.AIModel.id)).all()
    return {
        "models": [admin_model_out(db, model) for model in rows],
        "validation_required": benchmarks.validation_required(),
        "thresholds": benchmarks.thresholds(),
    }


@admin_router.patch("/models/{model_id}")
def admin_update_model(
    model_id: int,
    body: ModelUpdate,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Enable a model, make it the default for its purpose, or mark it exempt from benchmarking with a note."""
    model = db.get(models.AIModel, model_id)
    if model is None:
        raise HTTPException(status_code=404, detail="Model not found")
    fields = body.model_dump(exclude_unset=True)
    if body.benchmark_status == "exempt" and not (body.status_note or model.status_note).strip():
        raise HTTPException(status_code=422, detail="Explain why the model is exempt from benchmarking")
    if body.is_default:
        for other in db.scalars(select(models.AIModel).where(models.AIModel.purpose == model.purpose)):
            other.is_default = other.id == model.id
        fields.pop("is_default", None)
    for name, value in fields.items():
        setattr(model, name, value)
    if body.benchmark_status is None and model.benchmark_status != "exempt":
        benchmarks.refresh_model_status(db, model)
    db.commit()
    logger.info("Administrator %s updated model %s", admin.id, model.id)
    return admin_model_out(db, model)


# --- Administration: benchmarks ---


class BenchmarkRunIn(BaseModel):
    dataset_key: str = Field(min_length=1, max_length=100)
    ai_model_id: int | None = None


def benchmark_run_out(run: models.BenchmarkRun, with_details: bool = False) -> dict:
    out = {
        "id": run.id,
        "task": run.task,
        "dataset_key": run.dataset_key,
        "dataset_name": run.dataset_name,
        "dataset_sha256": run.dataset_sha256,
        "ai_model_id": run.ai_model_id,
        "model": run.model,
        "prompt_version": run.prompt_version,
        "status": run.status,
        "items": run.items,
        "processed": run.processed,
        "metrics": run.metrics,
        "thresholds": run.thresholds,
        "passed": run.passed,
        "error": run.error,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
    }
    if with_details:
        out["details"] = run.details
    return out


@admin_router.get("/benchmarks/datasets")
def admin_list_datasets():
    return {"datasets": [dataset.out() for dataset in benchmarks.available_datasets()]}


@admin_router.post("/benchmarks/datasets", status_code=201)
async def admin_upload_dataset(file: UploadFile = File(...), admin: models.User = Depends(require_admin)):
    """Add a benchmark data set as JSON. Build screening sets with scripts/fetch_synergy_dataset.py."""
    content = await file.read()
    try:
        dataset = benchmarks.save_dataset(content)
    except benchmarks.BenchmarkError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    logger.info("Administrator %s uploaded benchmark data set %s", admin.id, dataset.key)
    return dataset.out()


@admin_router.get("/benchmarks/runs")
def admin_list_runs(limit: int = Query(50, ge=1, le=200), db: Session = Depends(get_db)):
    rows = db.scalars(select(models.BenchmarkRun).order_by(models.BenchmarkRun.id.desc()).limit(limit))
    return [benchmark_run_out(row) for row in rows]


@admin_router.get("/benchmarks/runs/{run_id}")
def admin_get_run(run_id: int, db: Session = Depends(get_db)):
    run = db.get(models.BenchmarkRun, run_id)
    if run is None:
        raise HTTPException(status_code=404, detail="Benchmark run not found")
    return benchmark_run_out(run, with_details=True)


@admin_router.post("/benchmarks/runs", status_code=202)
async def admin_start_run(
    body: BenchmarkRunIn,
    admin: models.User = Depends(require_admin),
    db: Session = Depends(get_db),
):
    """Queue a benchmark run. The statistics data set checks the R engine and needs no model."""
    try:
        dataset = benchmarks.get_dataset(body.dataset_key)
    except benchmarks.BenchmarkError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    model = None
    if dataset.task != "statistics":
        if body.ai_model_id is None:
            raise HTTPException(status_code=422, detail="Choose the model to benchmark")
        model = db.get(models.AIModel, body.ai_model_id)
        if model is None or model.purpose != "chat":
            raise HTTPException(status_code=404, detail="That AI model isn't available")
    run = models.BenchmarkRun(
        task=dataset.task,
        dataset_key=dataset.key,
        dataset_name=dataset.name,
        dataset_sha256=dataset.sha256,
        ai_model_id=model.id if model else None,
        model=f"{model.provider}/{model.model_id}" if model else "R statistics engine",
        prompt_version=benchmarks.prompt_version(dataset.task),
        status="queued",
        items=dataset.size,
        thresholds=benchmarks.thresholds().get(dataset.task, {}),
        started_by_id=admin.id,
    )
    db.add(run)
    db.commit()
    try:
        await benchmarks.enqueue_benchmark(run.id)
    except Exception:
        # Without a worker (for example in tests), run it here so the result is still produced.
        logger.info("No benchmark worker available; running benchmark %s inline", run.id)
        await benchmarks.run_benchmark(db, run.id)
    db.refresh(run)
    return benchmark_run_out(run)


@admin_router.get("/reports/validation")
def admin_validation_report(db: Session = Depends(get_db)):
    """The model performance report: every catalog model, its latest benchmark results, and whether it's validated."""
    rows = db.scalars(
        select(models.AIModel).where(models.AIModel.purpose == "chat").order_by(models.AIModel.provider)
    ).all()
    return {
        "generated_at": models.utcnow(),
        "validation_required": benchmarks.validation_required(),
        "thresholds": benchmarks.thresholds(),
        "prompt_versions": {task: benchmarks.prompt_version(task) for task in benchmarks.MODEL_TASKS},
        "models": [admin_model_out(db, model) for model in rows],
    }


# --- Project calibration ---


class CalibrationIn(BaseModel):
    stage: Literal["title_abstract", "full_text"] = "title_abstract"
    sample_size: int = Field(30, ge=MIN_CALIBRATION_SAMPLE, le=MAX_CALIBRATION_SAMPLE)
    seed: int = Field(default_factory=lambda: random.SystemRandom().randint(1, 10**6))


class CalibrationAccept(BaseModel):
    note: str = Field("", max_length=2000)


def calibration_out(report: models.CalibrationReport, with_items: bool = False) -> dict:
    out = {
        "id": report.id,
        "stage": report.stage,
        "ai_model_id": report.ai_model_id,
        "model": report.model,
        "prompt_version": report.prompt_version,
        "sample_size": report.sample_size,
        "seed": report.seed,
        "status": report.status,
        "metrics": report.metrics,
        "thresholds": report.thresholds,
        "passed": report.passed,
        "error": report.error,
        "created_at": report.created_at,
        "accepted_at": report.accepted_at,
        "acceptance_note": report.acceptance_note,
    }
    if with_items:
        out["items"] = report.items
    return out


def accepted_calibration(
    db: Session, project_id: int, stage: str, model_id: int | None
) -> models.CalibrationReport | None:
    """The accepted calibration for this stage, model, and current prompt version, if there is one."""
    return db.scalar(
        select(models.CalibrationReport)
        .where(
            models.CalibrationReport.project_id == project_id,
            models.CalibrationReport.stage == stage,
            models.CalibrationReport.ai_model_id == model_id,
            models.CalibrationReport.prompt_version == SCREENING_PROMPT.id,
            models.CalibrationReport.accepted_at.is_not(None),
        )
        .order_by(models.CalibrationReport.id.desc())
        .limit(1)
    )


def _human_decision(record: models.Record, stage: str) -> str | None:
    """The record's settled human decision at this stage: an adjudication, or agreement among reviewers."""
    adjudication = next((a for a in record.adjudications if a.stage == stage), None)
    if adjudication is not None:
        return adjudication.decision if adjudication.decision in ("include", "exclude") else None
    decisions = {d.decision for d in record.decisions if d.stage == stage and d.decision in ("include", "exclude")}
    return decisions.pop() if len(decisions) == 1 else None


@router.get("/calibration")
def list_calibration(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.CalibrationReport)
        .where(models.CalibrationReport.project_id == access.project.id)
        .order_by(models.CalibrationReport.id.desc())
    ).all()
    settings = load_settings(db, access.project.id).screening
    return {
        "reports": [calibration_out(row) for row in rows],
        "require_ai_calibration": settings.require_ai_calibration,
        "recall_target": settings.recall_target,
        "current_prompt_version": SCREENING_PROMPT.id,
    }


@router.post("/calibration", status_code=201)
async def run_calibration(
    body: CalibrationIn,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Run the project's model over records reviewers have already decided, and report how well it agreed."""
    from ai_tasks import _accepted_criteria, paper_text

    stage = TITLE_ABSTRACT if body.stage == "title_abstract" else FULL_TEXT
    records = db.scalars(
        select(models.Record)
        .where(models.Record.project_id == access.project.id, models.Record.duplicate_of_id.is_(None))
        .options(selectinload(models.Record.decisions), selectinload(models.Record.adjudications))
    ).all()
    labelled = [(record, decision) for record in records if (decision := _human_decision(record, stage)) is not None]
    if len(labelled) < MIN_CALIBRATION_SAMPLE:
        raise HTTPException(
            status_code=409,
            detail=f"Calibration needs at least {MIN_CALIBRATION_SAMPLE} records reviewers have already decided",
        )
    if not any(decision == "include" for _, decision in labelled):
        raise HTTPException(status_code=409, detail="Calibration needs at least one included record to estimate recall")
    sample = random.Random(body.seed).sample(labelled, min(body.sample_size, len(labelled)))
    criteria = _accepted_criteria(db, access.project.id)
    ai = project_ai(db, access)
    settings = load_settings(db, access.project.id).screening
    report = models.CalibrationReport(
        project_id=access.project.id,
        stage=stage,
        ai_model_id=access.project.ai_model_id,
        model=f"{ai.provider.id}/{ai.model}",
        prompt_version=SCREENING_PROMPT.id,
        sample_size=len(sample),
        seed=body.seed,
        status="running",
        thresholds={"recall": settings.recall_target},
    )
    db.add(report)
    db.commit()

    items: list[dict[str, Any]] = []
    for start in range(0, len(sample), CALIBRATION_CHUNK):
        chunk = sample[start : start + CALIBRATION_CHUNK]
        outcomes = await asyncio.gather(
            *(evaluate_eligibility(ai, paper_text(record), criteria) for record, _ in chunk),
            return_exceptions=True,
        )
        for (record, human), outcome in zip(chunk, outcomes, strict=True):
            if isinstance(outcome, LLMError):
                items.append({"record_id": record.id, "human": human, "ai": None, "error": str(outcome)})
            elif isinstance(outcome, BaseException):
                logger.error("Calibration failed for record %s", record.id, exc_info=outcome)
                items.append({"record_id": record.id, "human": human, "ai": None, "error": "Unexpected error"})
            else:
                suggestion = "include" if outcome.value.decision in ("Include", "Maybe") else "exclude"
                items.append(
                    {
                        "record_id": record.id,
                        "human": human,
                        "ai": suggestion,
                        "confidence": outcome.value.confidence,
                        "error": None,
                    }
                )
    compared = [item for item in items if item["ai"] is not None]
    includes = [item for item in compared if item["human"] == "include"]
    excludes = [item for item in compared if item["human"] == "exclude"]
    found = sum(1 for item in includes if item["ai"] == "include")
    rejected = sum(1 for item in excludes if item["ai"] == "exclude")
    recall_interval = wilson_interval(found, len(includes)) if includes else None
    recall = found / len(includes) if includes else None
    metrics = {
        "compared": len(compared),
        "failed": len(items) - len(compared),
        "includes": len(includes),
        "excludes": len(excludes),
        "recall": recall,
        "recall_ci": list(recall_interval) if recall_interval else None,
        "specificity": rejected / len(excludes) if excludes else None,
        "agreement": (found + rejected) / len(compared) if compared else None,
    }
    report.items, report.metrics = items, metrics
    report.status = "completed" if compared else "failed"
    report.error = None if compared else "Every AI call failed; check the model and API key"
    report.passed = recall is not None and recall >= settings.recall_target
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="calibration.completed",
        entity_type="calibration_report",
        entity_id=report.id,
        details={"stage": stage, "model": report.model, "metrics": metrics, "passed": report.passed},
    )
    db.commit()
    return calibration_out(report, with_items=True)


@router.get("/calibration/{report_id}")
def get_calibration(
    report_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    report = get_in_project(db, models.CalibrationReport, report_id, access.project.id, "Calibration report")
    return calibration_out(report, with_items=True)


@router.post("/calibration/{report_id}/accept")
def accept_calibration(
    report_id: int,
    body: CalibrationAccept,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Accept a calibration report, which lets AI screening run when the project requires calibration."""
    report = get_in_project(db, models.CalibrationReport, report_id, access.project.id, "Calibration report")
    if report.status != "completed":
        raise HTTPException(status_code=409, detail="Only a completed calibration report can be accepted")
    if not report.passed and not body.note.strip():
        raise HTTPException(
            status_code=422, detail="This report is below the recall target; explain why you're accepting it"
        )
    report.accepted_by_id, report.accepted_at = access.user.id, models.utcnow()
    report.acceptance_note = body.note.strip()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="calibration.accepted",
        entity_type="calibration_report",
        entity_id=report.id,
        details={"passed": report.passed, "note": report.acceptance_note},
    )
    db.commit()
    return calibration_out(report)


# --- Bias monitoring ---


def _distribution(values: list[str], limit: int = 12) -> list[dict[str, Any]]:
    counts = Counter(value or "Not reported" for value in values)
    total = sum(counts.values()) or 1
    return [{"value": value, "count": count, "share": count / total} for value, count in counts.most_common(limit)]


@router.get("/bias-report")
def bias_report(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """Compares retrieved and included records by year, source, and venue, and reports the AI's error rates by group,
    so a systematic tilt in what reaches the review is visible."""
    records = db.scalars(
        select(models.Record)
        .where(models.Record.project_id == access.project.id, models.Record.duplicate_of_id.is_(None))
        .options(
            selectinload(models.Record.decisions),
            selectinload(models.Record.adjudications),
            selectinload(models.Record.search_run),
            selectinload(models.Record.ai_runs).selectinload(models.AIRun.screening),
        )
    ).all()
    included = [record for record in records if _human_decision(record, TITLE_ABSTRACT) == "include"]

    def compare(name: str, extract) -> dict[str, Any]:
        return {
            "field": name,
            "retrieved": _distribution([extract(record) for record in records]),
            "included": _distribution([extract(record) for record in included]),
        }

    groups = [
        compare("year", lambda record: record.year),
        compare("source", lambda record: record.search_run.database if record.search_run else ""),
        compare("venue", lambda record: record.venue),
    ]
    by_year: dict[str, dict[str, int]] = {}
    for record in records:
        human = _human_decision(record, TITLE_ABSTRACT)
        suggestion = next(
            (
                run.screening
                for run in reversed(record.ai_runs)
                if run.screening and run.screening.stage == TITLE_ABSTRACT
            ),
            None,
        )
        if human is None or suggestion is None:
            continue
        ai_decision = "include" if suggestion.decision in ("Include", "Maybe") else "exclude"
        bucket = by_year.setdefault(record.year or "Not reported", {"compared": 0, "missed": 0, "over_included": 0})
        bucket["compared"] += 1
        if human == "include" and ai_decision == "exclude":
            bucket["missed"] += 1
        if human == "exclude" and ai_decision == "include":
            bucket["over_included"] += 1
    confidences = [
        run.screening.confidence
        for record in records
        for run in record.ai_runs
        if run.screening and run.screening.confidence is not None
    ]
    return {
        "records": len(records),
        "included": len(included),
        "groups": groups,
        "ai_errors_by_year": [{"year": year, **counts} for year, counts in sorted(by_year.items())],
        "mean_ai_confidence": sum(confidences) / len(confidences) if confidences else None,
        "note": (
            "Language and country aren't extracted from records, so they aren't compared here; check them in the "
            "included studies' characteristics."
        ),
    }


# --- Reproducibility ---


def _numbers(value: Any, path: str = "") -> dict[str, float]:
    """Every number in a results tree, keyed by its path, so two runs can be compared value by value."""
    found: dict[str, float] = {}
    if isinstance(value, bool):
        return found
    if isinstance(value, int | float):
        return {path: float(value)}
    if isinstance(value, dict):
        for key, item in value.items():
            found.update(_numbers(item, f"{path}.{key}" if path else str(key)))
    elif isinstance(value, list):
        for position, item in enumerate(value):
            found.update(_numbers(item, f"{path}[{position}]"))
    return found


def check_out(check: models.ReproducibilityCheck) -> dict:
    return {
        "id": check.id,
        "run_id": check.run_id,
        "status": check.status,
        "max_abs_difference": check.max_abs_difference,
        "differences": check.differences,
        "r_version": check.r_version,
        "error": check.error,
        "created_at": check.created_at,
    }


@router.get("/reproducibility-checks")
def list_reproducibility_checks(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.ReproducibilityCheck)
        .where(models.ReproducibilityCheck.project_id == access.project.id)
        .order_by(models.ReproducibilityCheck.id.desc())
    )
    return [check_out(row) for row in rows]


@router.post("/analysis-runs/{run_id}/reproduce", status_code=201)
async def reproduce_analysis(
    run_id: int,
    tolerance: float = Query(1e-6, ge=0, le=1),
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    """Rerun an archived analysis from its stored specification and data, and compare every number with the original."""
    from stats_engine import ANALYSIS_TYPES, StatsEngineUnavailable, StatsRunError, run_analysis

    run = get_in_project(db, models.AnalysisRun, run_id, access.project.id, "Analysis run")
    if run.status != "succeeded" or not run.results:
        raise HTTPException(status_code=409, detail="Only a successful run can be reproduced")
    analysis = db.get(models.Analysis, run.analysis_id)
    if analysis is None or analysis.analysis_type not in ANALYSIS_TYPES:
        raise HTTPException(status_code=409, detail="That analysis can no longer be reproduced")
    if analysis.analysis_type == "ipd":
        raise HTTPException(
            status_code=409,
            detail="Individual participant data analyses are rerun from their files in the synthesis screen",
        )
    template, needed = ANALYSIS_TYPES[analysis.analysis_type]
    check = models.ReproducibilityCheck(
        project_id=access.project.id, run_id=run.id, status="failed", created_by_id=access.user.id
    )
    try:
        output = await asyncio.to_thread(run_analysis, template, run.dataset["r_spec"], run.dataset["rows"], needed)
    except (StatsRunError, StatsEngineUnavailable) as exc:
        check.error = str(exc)
        db.add(check)
        db.commit()
        return check_out(check)
    original, repeated = _numbers(run.results), _numbers(output.results)
    differences = []
    largest = 0.0
    for path, value in sorted(original.items()):
        again = repeated.get(path)
        if again is None:
            differences.append({"path": path, "original": value, "repeated": None})
            continue
        gap = abs(value - again)
        if gap > 0:
            largest = max(largest, gap)
        if gap > tolerance * max(abs(value), 1.0):
            differences.append({"path": path, "original": value, "repeated": again, "difference": gap})
    missing = sorted(set(original) - set(repeated))
    check.status = (
        "identical" if largest == 0 and not missing else ("within_tolerance" if not differences else "different")
    )
    check.max_abs_difference = largest
    check.differences = differences[:200]
    check.r_version = output.r_version
    check.error = None
    db.add(check)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="analysis.reproduced",
        entity_type="analysis_run",
        entity_id=run.id,
        details={"status": check.status, "max_abs_difference": largest, "r_version": output.r_version},
    )
    db.commit()
    return check_out(check)


# --- Quality reports ---


@router.get("/reports/sop")
def sop_report(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)):
    """The standard operating procedure this review followed: its settings, gates, and who signed each one off."""
    settings = load_settings(db, access.project.id)
    stages = db.scalars(
        select(models.ProjectStage)
        .where(models.ProjectStage.project_id == access.project.id)
        .options(selectinload(models.ProjectStage.completed_by))
    ).all()
    by_stage = {row.stage: row for row in stages}

    def stage_out(stage: str) -> dict[str, Any]:
        row = by_stage.get(stage)
        signer = row.completed_by if row is not None else None
        return {
            "stage": stage,
            "label": STAGE_LABELS[stage],
            "completed_at": row.completed_at if row is not None else None,
            "completed_by": signer.full_name if signer is not None else None,
            "note": row.completion_note if row is not None else None,
        }

    return {
        "project": {"id": access.project.id, "title": access.project.title},
        "generated_at": models.utcnow(),
        "settings": settings.model_dump(),
        "stages": [stage_out(stage) for stage in STAGES],
    }


@router.get("/reports/provenance")
def provenance_report(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_AUDIT)), db: Session = Depends(get_db)
):
    """Where the review's data came from: searches, imports, documents, AI use, and the audit chain."""
    from audit import verify_chain

    runs = db.scalars(
        select(models.SearchRun).where(models.SearchRun.project_id == access.project.id).order_by(models.SearchRun.id)
    ).all()
    documents = db.execute(
        select(models.Document.origin, func.count())
        .join(models.Record, models.Record.id == models.Document.record_id)
        .where(models.Record.project_id == access.project.id)
        .group_by(models.Document.origin)
    ).all()
    return {
        "project": {"id": access.project.id, "title": access.project.title},
        "generated_at": models.utcnow(),
        "searches": [
            {
                "id": run.id,
                "kind": run.kind,
                "database": run.database,
                "interface": run.interface,
                "query": run.query,
                "searched_on": run.searched_on,
                "results": run.result_count,
                "executed_at": run.executed_at,
            }
            for run in runs
        ],
        "documents_by_origin": {origin: count for origin, count in documents},
        "ai_use": ai_use(db, access.project.id),
        "audit": {
            "events": db.scalar(
                select(func.count())
                .select_from(models.AuditEvent)
                .where(models.AuditEvent.project_id == access.project.id)
            )
            or 0,
            "chain_valid": verify_chain(db, access.project.id),
        },
    }


@router.get("/reports/validation")
def project_validation_report(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """How the AI used in this project was validated: the catalog's benchmarks, and the project's own calibration."""
    model = access.project.ai_model
    reports = db.scalars(
        select(models.CalibrationReport)
        .where(models.CalibrationReport.project_id == access.project.id)
        .order_by(models.CalibrationReport.id.desc())
    ).all()
    return {
        "project": {"id": access.project.id, "title": access.project.title},
        "generated_at": models.utcnow(),
        "model": admin_model_out(db, model) if model is not None else None,
        "validation_required": benchmarks.validation_required(),
        "calibration": [calibration_out(report) for report in reports],
        "require_ai_calibration": load_settings(db, access.project.id).screening.require_ai_calibration,
    }
