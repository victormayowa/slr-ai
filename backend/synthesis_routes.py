"""Statistical synthesis: the R engine's status, analyses (specification, pre-specified or post hoc, statistician
approval), runs in R with their results, plots, and exported code, the analysis data set with a pooling check, and
individual participant data (stored encrypted, every access audited).
"""

import csv
import hashlib
import io
import json
from typing import Literal

from fastapi import APIRouter, Depends, File, HTTPException, Response, UploadFile
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import crypto
import models
from appraisal_routes import study_designs
from audit import record_event
from code_export import bundle_files
from database import get_db
from extraction_data import included_studies
from jobs import job_out, start_job
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from stats_engine import ANALYSIS_TYPES, capabilities, engine_status, export_bundle
from storage import document_storage
from synthesis_data import AnalysisSpec, DatasetError, build_dataset, ipd_context, pooling_assessment
from workflow import require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["synthesis"])

MAX_IPD_BYTES = 20 * 1024 * 1024
ANALYSIS_TYPE_LABELS = {
    "pairwise": "Pairwise meta-analysis",
    "nma": "Network meta-analysis",
    "dta": "Diagnostic test accuracy meta-analysis",
    "bayesian": "Bayesian meta-analysis",
    "rve": "Dependent effects (robust variance estimation)",
    "ipd": "Individual participant data meta-analysis",
    "swim": "Synthesis without meta-analysis (SWiM)",
}


def _audit(db: Session, access: ProjectAccess, action: str, entity_type: str, entity_id: int, details: dict) -> None:
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=action,
        entity_type=entity_type,
        entity_id=entity_id,
        details=details,
    )


def _sha(content: object) -> str:
    return hashlib.sha256(json.dumps(content, sort_keys=True, default=str).encode()).hexdigest()


def plan_items(project: models.Project) -> dict[str, str]:
    """Names in the pre-specified analysis plan (lowercased) and their kind."""
    plan = (project.protocol.analysis_plan if project.protocol else None) or {}
    items = {str(o.get("name", "")).casefold(): "outcome" for o in plan.get("outcomes", [])}
    items.update({str(s.get("name", "")).casefold(): "subgroup" for s in plan.get("subgroups", [])})
    items.update({str(s.get("name", "")).casefold(): "sensitivity" for s in plan.get("sensitivity_analyses", [])})
    items.pop("", None)
    return items


def run_summary(run: models.AnalysisRun) -> dict:
    return {
        "id": run.id,
        "status": run.status,
        "is_final": run.is_final,
        "error": run.error,
        "created_at": run.created_at,
        "finished_at": run.finished_at,
        "duration_ms": run.duration_ms,
        "source": run.dataset.get("source"),
        "studies": len({row.get("study_id") for row in run.dataset.get("rows", [])}),
    }


def final_run(analysis: models.Analysis) -> models.AnalysisRun | None:
    """The latest successful final run of the analysis as currently specified (a revised specification needs one)."""
    current = _sha(analysis.spec)
    runs = reversed(analysis.runs)
    return next((r for r in runs if r.is_final and r.status == "succeeded" and r.spec_sha256 == current), None)


def analysis_out(analysis: models.Analysis) -> dict:
    final = final_run(analysis)
    return {
        "id": analysis.id,
        "title": analysis.title,
        "outcome": analysis.outcome,
        "analysis_type": analysis.analysis_type,
        "analysis_type_label": ANALYSIS_TYPE_LABELS.get(analysis.analysis_type, analysis.analysis_type),
        "spec": analysis.spec,
        "prespecified": analysis.prespecified,
        "plan_reference": analysis.plan_reference,
        "justification": analysis.justification,
        "status": analysis.status,
        "approved_by": analysis.approved_by.full_name if analysis.approved_by else None,
        "approved_at": analysis.approved_at,
        "approval_note": analysis.approval_note,
        "created_at": analysis.created_at,
        "updated_at": analysis.updated_at,
        "runs": [run_summary(r) for r in reversed(analysis.runs)],
        "final_run_id": final.id if final else None,
    }


def run_out(run: models.AnalysisRun) -> dict:
    return {
        **run_summary(run),
        "analysis_id": run.analysis_id,
        "spec": run.spec,
        "spec_sha256": run.spec_sha256,
        "dataset": run.dataset,
        "dataset_sha256": run.dataset_sha256,
        "extraction_snapshot_id": run.extraction_snapshot_id,
        "seed": run.seed,
        "results": run.results,
        "plots": [{k: v for k, v in plot.items() if k != "storage_key"} for plot in run.plots],
        "script": run.script,
        "session_info": run.session_info,
        "r_version": run.r_version,
        "packages": run.packages,
        "log": run.log,
    }


def _analysis(db: Session, access: ProjectAccess, analysis_id: int) -> models.Analysis:
    return get_in_project(db, models.Analysis, analysis_id, access.project.id, "Analysis")


def _run(db: Session, access: ProjectAccess, run_id: int) -> models.AnalysisRun:
    return get_in_project(db, models.AnalysisRun, run_id, access.project.id, "Analysis run")


def _validate(
    db: Session,
    access: ProjectAccess,
    analysis_type: str,
    spec: dict,
    prespecified: bool,
    plan_reference: str,
    justification: str,
) -> AnalysisSpec:
    if analysis_type not in ANALYSIS_TYPES:
        raise HTTPException(status_code=422, detail=f"Unknown analysis type: {analysis_type}")
    try:
        parsed = AnalysisSpec.model_validate(spec)
    except ValidationError as exc:
        raise HTTPException(
            status_code=422, detail=f"Invalid analysis specification: {exc.errors()[0]['msg']}"
        ) from exc
    if prespecified and plan_reference.strip().casefold() not in plan_items(access.project):
        raise HTTPException(
            status_code=422,
            detail="A pre-specified analysis must name the outcome, subgroup, or sensitivity analysis it implements "
            "from the protocol's analysis plan",
        )
    if not prespecified and len(justification.strip()) < 10:
        raise HTTPException(
            status_code=422, detail="Explain why this post hoc analysis is needed (it will be reported as post hoc)"
        )
    return parsed


@router.get("/statistics/engine")
def statistics_engine(refresh: bool = False, access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    return {**capabilities(engine_status(refresh=refresh)), "analysis_type_labels": ANALYSIS_TYPE_LABELS}


class AnalysisIn(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    outcome: str = Field(min_length=1, max_length=300)
    analysis_type: str = Field(max_length=20)
    spec: dict = Field(default_factory=dict)
    prespecified: bool = True
    plan_reference: str = Field("", max_length=300)
    justification: str = Field("", max_length=5000)


@router.get("/analyses")
def list_analyses(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    analyses = db.scalars(
        select(models.Analysis).where(models.Analysis.project_id == access.project.id).order_by(models.Analysis.id)
    )
    return {"analyses": [analysis_out(a) for a in analyses], "plan": plan_items(access.project)}


@router.post("/analyses", status_code=201)
def create_analysis(
    body: AnalysisIn,
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "synthesis")
    spec = _validate(
        db, access, body.analysis_type, body.spec, body.prespecified, body.plan_reference, body.justification
    )
    analysis = models.Analysis(
        project_id=access.project.id,
        title=body.title.strip(),
        outcome=body.outcome.strip(),
        analysis_type=body.analysis_type,
        spec=spec.model_dump(),
        prespecified=body.prespecified,
        plan_reference=body.plan_reference.strip(),
        justification=body.justification.strip(),
        status="draft",
        created_by_id=access.user.id,
    )
    db.add(analysis)
    db.flush()
    _audit(
        db,
        access,
        "analysis.created",
        "analysis",
        analysis.id,
        {
            "analysis_type": body.analysis_type,
            "outcome": analysis.outcome,
            "prespecified": body.prespecified,
            "spec": analysis.spec,
        },
    )
    db.commit()
    db.refresh(analysis)
    return analysis_out(analysis)


@router.get("/analyses/{analysis_id}")
def get_analysis(
    analysis_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    return analysis_out(_analysis(db, access, analysis_id))


@router.put("/analyses/{analysis_id}")
def update_analysis(
    analysis_id: int,
    body: AnalysisIn,
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    """Revise an analysis. Changing an approved analysis returns it to draft for a new approval."""
    require_stage_open(db, access.project.id, "synthesis")
    analysis = _analysis(db, access, analysis_id)
    spec = _validate(
        db, access, body.analysis_type, body.spec, body.prespecified, body.plan_reference, body.justification
    )
    before = {
        "title": analysis.title,
        "outcome": analysis.outcome,
        "analysis_type": analysis.analysis_type,
        "spec": analysis.spec,
        "prespecified": analysis.prespecified,
    }
    analysis.title, analysis.outcome, analysis.analysis_type = (
        body.title.strip(),
        body.outcome.strip(),
        body.analysis_type,
    )
    analysis.spec, analysis.prespecified = spec.model_dump(), body.prespecified
    analysis.plan_reference, analysis.justification = body.plan_reference.strip(), body.justification.strip()
    after = {
        "title": analysis.title,
        "outcome": analysis.outcome,
        "analysis_type": analysis.analysis_type,
        "spec": analysis.spec,
        "prespecified": analysis.prespecified,
    }
    approval_withdrawn = analysis.status == "approved" and before != after
    if approval_withdrawn:
        analysis.status, analysis.approved_by_id, analysis.approved_at, analysis.approval_note = "draft", None, None, ""
    _audit(
        db,
        access,
        "analysis.revised",
        "analysis",
        analysis.id,
        {"before": before, "after": after, "approval_withdrawn": approval_withdrawn},
    )
    db.commit()
    db.refresh(analysis)
    return analysis_out(analysis)


class ApprovalIn(BaseModel):
    note: str = Field(min_length=10, max_length=5000)


@router.post("/analyses/{analysis_id}/approve")
def approve_analysis(
    analysis_id: int,
    body: ApprovalIn,
    access: ProjectAccess = Depends(project_access(Permission.APPROVE_ANALYSIS)),
    db: Session = Depends(get_db),
):
    """A statistician approves the model choice, so runs on the locked data set become final."""
    require_stage_open(db, access.project.id, "synthesis")
    analysis = _analysis(db, access, analysis_id)
    analysis.status, analysis.approved_by_id, analysis.approved_at = "approved", access.user.id, models.utcnow()
    analysis.approval_note = body.note.strip()
    _audit(
        db,
        access,
        "analysis.approved",
        "analysis",
        analysis.id,
        {"note": analysis.approval_note, "spec_sha256": _sha(analysis.spec)},
    )
    db.commit()
    db.refresh(analysis)
    return analysis_out(analysis)


@router.delete("/analyses/{analysis_id}", status_code=204)
def delete_analysis(
    analysis_id: int,
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "synthesis")
    analysis = _analysis(db, access, analysis_id)
    if final_run(analysis) is not None:
        raise HTTPException(
            status_code=409, detail="An analysis with final results can't be deleted; revise it instead"
        )
    _audit(db, access, "analysis.deleted", "analysis", analysis.id, {"title": analysis.title})
    db.delete(analysis)
    db.commit()
    return Response(status_code=204)


def _build(db: Session, access: ProjectAccess, analysis: models.Analysis):
    try:
        return build_dataset(
            db, access.project.id, analysis.analysis_type, AnalysisSpec.model_validate(analysis.spec), analysis.outcome
        )
    except DatasetError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/analyses/{analysis_id}/dataset")
def analysis_dataset(
    analysis_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """The data set the analysis would run on now, the studies left out and why, and whether pooling looks sensible."""
    analysis = _analysis(db, access, analysis_id)
    built = _build(db, access, analysis)
    designs = study_designs(db, access.project.id, [s.id for s in included_studies(db, access.project.id)])
    if analysis.analysis_type == "ipd":
        _audit(db, access, "ipd.accessed", "analysis", analysis.id, {"purpose": "data set preview"})
        db.commit()
    return {
        "source": built.source,
        "rows": built.rows,
        "excluded": built.excluded,
        "r_spec": built.r_spec,
        "sha256": built.sha256,
        "pooling": pooling_assessment(analysis.analysis_type, built, designs),
    }


@router.post("/analyses/{analysis_id}/runs", status_code=202)
async def start_run(
    analysis_id: int,
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    """Run the analysis in R. Runs of an approved analysis on the locked extraction data set are final."""
    require_stage_open(db, access.project.id, "synthesis")
    analysis = _analysis(db, access, analysis_id)
    status = engine_status()
    _, needed = ANALYSIS_TYPES[analysis.analysis_type]
    if not status.available or status.missing(needed):
        missing = status.missing(needed)
        raise HTTPException(status_code=503, detail=status.message or f"R packages missing: {', '.join(missing)}")
    built = _build(db, access, analysis)
    if not built.rows:
        raise HTTPException(status_code=422, detail="No study has usable data for this analysis")
    run = models.AnalysisRun(
        analysis_id=analysis.id,
        project_id=access.project.id,
        status="queued",
        spec=analysis.spec,
        spec_sha256=_sha(analysis.spec),
        dataset={"rows": built.rows, "excluded": built.excluded, "source": built.source, "r_spec": built.r_spec},
        dataset_sha256=built.sha256,
        extraction_snapshot_id=built.snapshot_id,
        seed=int(analysis.spec.get("seed") or 20260915),
        is_final=analysis.status == "approved" and built.source == "locked",
        started_by_id=access.user.id,
    )
    db.add(run)
    db.flush()
    _audit(
        db,
        access,
        "analysis.run_queued",
        "analysis_run",
        run.id,
        {"analysis_id": analysis.id, "is_final": run.is_final, "dataset_sha256": run.dataset_sha256},
    )
    if analysis.analysis_type == "ipd":
        _audit(db, access, "ipd.accessed", "analysis_run", run.id, {"purpose": "analysis run"})
    db.commit()
    job = await start_job(db, access, "statistics", [run.id])
    db.refresh(run)
    return {"job": job_out(job), "run": run_out(run)}


@router.get("/analysis-runs/{run_id}")
def get_run(
    run_id: int, access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    return run_out(_run(db, access, run_id))


@router.get("/analysis-runs/{run_id}/plots/{name}.{fmt}")
def run_plot(
    run_id: int,
    name: str,
    fmt: str,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    run = _run(db, access, run_id)
    plot = next((p for p in run.plots if p["name"] == name and p["format"] == fmt), None)
    if plot is None:
        raise HTTPException(status_code=404, detail="Plot not found")
    content = document_storage().read(plot["storage_key"])
    disposition = "inline" if fmt in ("svg", "png") else "attachment"
    return Response(
        content,
        media_type=plot["media_type"],
        headers={"Content-Disposition": f'{disposition}; filename="run-{run.id}-{name}.{fmt}"'},
    )


@router.get("/analysis-runs/{run_id}/export")
def export_run(
    run_id: int, access: ProjectAccess = Depends(project_access(Permission.EXPORT)), db: Session = Depends(get_db)
):
    """The run's script, specification, data, results, and session details as a zip that reruns outside OmniReview."""
    run = _run(db, access, run_id)
    files = bundle_files(run, run.analysis.title)
    storage = document_storage()
    plots = {f"plots/{p['name']}.{p['format']}": storage.read(p["storage_key"]) for p in run.plots}
    _audit(db, access, "analysis.run_exported", "analysis_run", run.id, {})
    db.commit()
    return Response(
        export_bundle(files, plots),
        media_type="application/zip",
        headers={"Content-Disposition": f'attachment; filename="analysis-{run.analysis_id}-run-{run.id}.zip"'},
    )


# --- Individual participant data ---


def ipd_out(dataset: models.IPDDataset) -> dict:
    return {
        "id": dataset.id,
        "study_id": dataset.study_id,
        "file_name": dataset.file_name,
        "sha256": dataset.sha256,
        "rows": dataset.rows,
        "columns": dataset.columns,
        "mapping": dataset.mapping,
        "validation": dataset.validation,
        "created_at": dataset.created_at,
    }


def _read_ipd(dataset: models.IPDDataset) -> list[dict[str, str]]:
    text = crypto.decrypt_bytes(document_storage().read(dataset.storage_key), ipd_context(dataset)).decode("utf-8-sig")
    return list(csv.DictReader(io.StringIO(text)))


def validate_ipd(rows: list[dict[str, str]], mapping: dict[str, str], outcome_type: str) -> dict:
    """Per-variable checks: missing values, treatment coded 0/1, and a binary outcome coded 0/1 or a numeric one."""
    problems: list[str] = []
    summary: dict[str, dict] = {}
    for variable, column in mapping.items():
        values = [(row.get(column) or "").strip() for row in rows]
        missing = sum(1 for v in values if v == "")
        present = [v for v in values if v != ""]
        numeric = all(_is_number(v) for v in present)
        summary[variable] = {"column": column, "missing": missing, "numeric": numeric, "distinct": len(set(present))}
        if variable == "treatment" and set(present) - {"0", "1"}:
            problems.append("Code treatment as 1 (intervention) and 0 (control)")
        if variable == "outcome":
            if outcome_type == "binary" and set(present) - {"0", "1"}:
                problems.append("Code a binary outcome as 1 (event) and 0 (no event)")
            if outcome_type == "continuous" and not numeric:
                problems.append("A continuous outcome must be numeric")
        if missing:
            problems.append(f"{variable} is missing for {missing} participants; they are left out of analyses")
    return {"variables": summary, "problems": problems, "outcome_type": outcome_type}


def _is_number(value: str) -> bool:
    try:
        float(value)
    except ValueError:
        return False
    return True


@router.get("/ipd")
def list_ipd(access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)), db: Session = Depends(get_db)):
    datasets = db.scalars(
        select(models.IPDDataset)
        .where(models.IPDDataset.project_id == access.project.id)
        .order_by(models.IPDDataset.id)
    )
    return [ipd_out(d) for d in datasets]


@router.post("/studies/{study_id}/ipd", status_code=201)
async def upload_ipd(
    study_id: int,
    file: UploadFile = File(...),
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    """Upload one study's participant data as CSV (one row per participant). The file is encrypted at rest."""
    require_stage_open(db, access.project.id, "synthesis")
    if study_id not in {s.id for s in included_studies(db, access.project.id)}:
        raise HTTPException(status_code=404, detail="Only studies included in the review can have participant data")
    content = await file.read(MAX_IPD_BYTES + 1)
    if len(content) > MAX_IPD_BYTES:
        raise HTTPException(status_code=413, detail="Participant data files are limited to 20 MB")
    try:
        text = content.decode("utf-8-sig")
        reader = csv.DictReader(io.StringIO(text))
        rows = list(reader)
        columns = [c for c in (reader.fieldnames or []) if c]
    except (UnicodeDecodeError, csv.Error) as exc:
        raise HTTPException(status_code=422, detail="Upload a UTF-8 CSV file with a header row") from exc
    if not columns or not rows:
        raise HTTPException(status_code=422, detail="The file has no header row or no participants")
    existing = db.scalar(
        select(models.IPDDataset).where(
            models.IPDDataset.project_id == access.project.id, models.IPDDataset.study_id == study_id
        )
    )
    storage = document_storage()
    dataset = existing or models.IPDDataset(project_id=access.project.id, study_id=study_id)
    if existing is not None:
        storage.delete(existing.storage_key)
    dataset.file_name = (file.filename or "participants.csv")[:255]
    dataset.sha256 = hashlib.sha256(content).hexdigest()
    dataset.storage_key = storage.save(
        access.project.id, crypto.encrypt_bytes(content, f"ipd:{access.project.id}:{study_id}")
    )
    dataset.rows, dataset.columns, dataset.mapping, dataset.validation = len(rows), columns, {}, {}
    dataset.uploaded_by_id = access.user.id
    db.add(dataset)
    db.flush()
    _audit(
        db,
        access,
        "ipd.uploaded",
        "ipd_dataset",
        dataset.id,
        {"study_id": study_id, "rows": len(rows), "sha256": dataset.sha256, "replaced": existing is not None},
    )
    db.commit()
    return ipd_out(dataset)


class IPDMapping(BaseModel):
    treatment: str = Field(min_length=1, max_length=200)
    outcome: str = Field(min_length=1, max_length=200)
    outcome_type: Literal["binary", "continuous"] = "binary"
    covariates: dict[str, str] = Field(default_factory=dict)


@router.put("/ipd/{dataset_id}/mapping")
def map_ipd(
    dataset_id: int,
    body: IPDMapping,
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    """Map standard variables (treatment, outcome, named covariates) to the file's columns and validate them."""
    require_stage_open(db, access.project.id, "synthesis")
    dataset = get_in_project(db, models.IPDDataset, dataset_id, access.project.id, "Participant data")
    mapping = {"treatment": body.treatment, "outcome": body.outcome, **body.covariates}
    unknown = [column for column in mapping.values() if column not in dataset.columns]
    if unknown:
        raise HTTPException(status_code=422, detail=f"These columns aren't in the file: {', '.join(unknown)}")
    for name in body.covariates:
        if not name.isidentifier() or name in ("study", "treatment", "outcome"):
            raise HTTPException(status_code=422, detail=f"{name} can't be used as a covariate name")
    dataset.mapping = mapping
    dataset.validation = validate_ipd(_read_ipd(dataset), mapping, body.outcome_type)
    _audit(
        db,
        access,
        "ipd.mapped",
        "ipd_dataset",
        dataset.id,
        {"mapping": mapping, "problems": dataset.validation["problems"]},
    )
    db.commit()
    return ipd_out(dataset)


@router.delete("/ipd/{dataset_id}", status_code=204)
def delete_ipd(
    dataset_id: int,
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "synthesis")
    dataset = get_in_project(db, models.IPDDataset, dataset_id, access.project.id, "Participant data")
    document_storage().delete(dataset.storage_key)
    _audit(db, access, "ipd.deleted", "ipd_dataset", dataset.id, {"study_id": dataset.study_id})
    db.delete(dataset)
    db.commit()
    return Response(status_code=204)
