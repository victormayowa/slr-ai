"""Review stages and the gates between them.

Stages run in a fixed order, and a stage accepts changes only while it is open: every earlier stage is completed and it
is not. Completing a stage checks its requirements, records who signed it off and why, and stores a hashed snapshot of
the stage's content (for the protocol, that snapshot is the next protocol version). Reopening a completed stage needs a
rationale and also reopens every later stage, because their work may depend on what changes.
"""

import hashlib
import json
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session, selectinload

import models
from audit import record_event
from dedup import candidate_pairs, find_duplicates, reviewed_pairs
from extraction_data import dataset_content, extraction_progress, included_records, included_studies
from permissions import Permission
from prisma_flow import accepted_stopping
from protocol_design import project_sections, protocol_issues
from protocol_frameworks import PROTOCOL_SECTIONS, REQUIRED_SECTIONS
from review_data import FULL_TEXT, TITLE_ABSTRACT, ReviewPolicy
from review_settings import review_policy
from search_quality import press_status
from studies import ensure_studies

STAGES = ["protocol", "search", "screening", "full_text_screening", "extraction", "appraisal", "synthesis", "certainty"]

STAGE_LABELS = {
    "protocol": "Protocol",
    "search": "Search and deduplication",
    "screening": "Title and abstract screening",
    "full_text_screening": "Full-text screening",
    "extraction": "Data extraction",
    "appraisal": "Risk of bias assessment",
    "synthesis": "Synthesis",
    "certainty": "Certainty of evidence (GRADE)",
}

# Who may complete or reopen each stage.
STAGE_PERMISSIONS = {
    "protocol": Permission.APPROVE_PROTOCOL,
    "search": Permission.MANAGE_WORKFLOW,
    "screening": Permission.MANAGE_WORKFLOW,
    "full_text_screening": Permission.MANAGE_WORKFLOW,
    "extraction": Permission.MANAGE_WORKFLOW,
    "appraisal": Permission.MANAGE_WORKFLOW,
    "synthesis": Permission.APPROVE_ANALYSIS,
    "certainty": Permission.APPROVE_CERTAINTY,
}

PROTOCOL_FIELDS = (
    "review_type",
    "framework",
    "description",
    "suggested_criteria",
    "extraction_outline",
    "rob_tool",
    "question",
    "question_elements",
    "finer",
    "analysis_plan",
)


class WorkflowError(Exception):
    """A change the workflow doesn't allow right now. The message is safe to show users."""

    def __init__(self, message: str, status_code: int = 409) -> None:
        super().__init__(message)
        self.status_code = status_code


@dataclass
class Requirement:
    label: str
    met: bool


def _check_stage_name(stage: str) -> None:
    if stage not in STAGES:
        raise WorkflowError(f"Unknown workflow stage: {stage}", status_code=404)


def _stage_rows(db: Session, project_id: int) -> dict[str, models.ProjectStage]:
    rows = db.scalars(select(models.ProjectStage).where(models.ProjectStage.project_id == project_id))
    return {row.stage: row for row in rows}


def _is_completed(rows: dict[str, models.ProjectStage], stage: str) -> bool:
    row = rows.get(stage)
    return row is not None and row.completed_at is not None


def _status(rows: dict[str, models.ProjectStage], stage: str) -> str:
    if _is_completed(rows, stage):
        return "completed"
    earlier = STAGES[: STAGES.index(stage)]
    return "open" if all(_is_completed(rows, name) for name in earlier) else "not_started"


def require_stage_open(db: Session, project_id: int, stage: str) -> None:
    """Raise WorkflowError unless the stage currently accepts changes."""
    _check_stage_name(stage)
    rows = _stage_rows(db, project_id)
    status = _status(rows, stage)
    if status == "completed":
        raise WorkflowError(f"{STAGE_LABELS[stage]} is completed. Reopen it with a rationale to make changes.")
    if status == "not_started":
        blocking = next(name for name in STAGES[: STAGES.index(stage)] if not _is_completed(rows, name))
        raise WorkflowError(f"Complete {STAGE_LABELS[blocking]} before working on {STAGE_LABELS[stage]}.")


def _load_records(db: Session, project_id: int) -> list[models.Record]:
    return list(
        db.scalars(
            select(models.Record)
            .where(models.Record.project_id == project_id)
            .options(
                selectinload(models.Record.decisions),
                selectinload(models.Record.adjudications),
                selectinload(models.Record.ai_runs).selectinload(models.AIRun.appraisal),
                selectinload(models.Record.ai_runs)
                .selectinload(models.AIRun.extraction_values)
                .selectinload(models.ExtractionSuggestion.field),
            )
            .order_by(models.Record.id)
        )
    )


def _included(records: list[models.Record], policy: ReviewPolicy) -> list[models.Record]:
    return [r for r in records if policy.included(r)]


def _decisions_out(record: models.Record, stage: str) -> list[dict[str, Any]]:
    return [
        {"reviewer_id": d.reviewer_id, "decision": d.decision, "reason_code": d.reason_code}
        for d in record.decisions
        if d.stage == stage
    ]


def _count(db: Session, model: Any, project_id: int) -> int:
    return db.scalar(select(func.count()).select_from(model).where(model.project_id == project_id)) or 0


def requirements(db: Session, project: models.Project, stage: str) -> list[Requirement]:
    """What must be true before the stage can be completed."""
    _check_stage_name(stage)
    if stage == "protocol":
        protocol = project.protocol
        criteria = db.scalars(select(models.Criterion).where(models.Criterion.project_id == project.id)).all()
        issue_codes = {issue.code for issue in protocol_issues(db, project) if issue.severity == "error"}
        required_sections = ", ".join(section.label for section in REQUIRED_SECTIONS)
        return [
            Requirement("Study description written", bool(protocol and protocol.description.strip())),
            Requirement(
                "Review question and every framework element written",
                not issue_codes & {"no_protocol", "question_missing", "framework_unknown", "element_missing"},
            ),
            Requirement(
                "At least one accepted inclusion criterion",
                any(c.kind == "inclusion" and c.status == "accepted" for c in criteria),
            ),
            Requirement("Every suggested criterion accepted or rejected", all(c.status != "pending" for c in criteria)),
            Requirement("No criterion both included and excluded", "contradictory_criteria" not in issue_codes),
            Requirement("At least one search strategy", _count(db, models.SearchStrategy, project.id) > 0),
            Requirement("At least one extraction field", bool(project.extraction_fields)),
            Requirement("At least one primary outcome pre-specified", "no_primary_outcome" not in issue_codes),
            Requirement(
                f"Required protocol sections written ({required_sections})", "section_missing" not in issue_codes
            ),
        ]
    if stage in EVIDENCE_STAGES:
        return _evidence_requirements(db, project, stage)

    records = _load_records(db, project.id)
    if stage == "search":
        registrations = db.scalars(
            select(models.ProtocolRegistration).where(models.ProtocolRegistration.project_id == project.id)
        ).all()
        recorded = [r for r in registrations if r.status in ("submitted", "registered", "waived")]
        search_requirements = [
            Requirement("At least one database search or file import", _count(db, models.SearchRun, project.id) > 0),
            Requirement("Deduplication run on every record", not find_duplicates(records)),
            Requirement("Possible duplicates reviewed", not candidate_pairs(records, reviewed_pairs(db, project.id))),
            Requirement("Protocol registration submitted, or registration waived with a reason", bool(recorded)),
            Requirement(
                "PRESS peer review approved for every current search strategy, or PRESS waived with a reason",
                press_status(db, project.id)["met"],
            ),
        ]
        latest_version = _latest_version(db, project.id, "protocol")
        if any(r.status != "waived" and r.protocol_version < latest_version for r in recorded):
            search_requirements.append(
                Requirement(f"Registry record updated with protocol version {latest_version}", False)
            )
        return search_requirements
    policy = review_policy(db, project.id)
    unique = [r for r in records if r.duplicate_of_id is None]
    if stage == "screening":
        statuses = [(r, policy.status(r, TITLE_ABSTRACT)) for r in unique]
        stop = accepted_stopping(db, project.id, TITLE_ABSTRACT, len(unique))
        # An accepted stopping rule covers records nobody has screened, but never a record a reviewer wants to include.
        uncovered = [
            r
            for r, status in statuses
            if status.final not in ("include", "exclude")
            and (stop is None or any(d.decision == "include" for d in r.decisions if d.stage == TITLE_ABSTRACT))
        ]
        screening_requirements = [
            Requirement(
                "Every record has an include or exclude decision (or an accepted stopping rule covers the rest)",
                not uncovered,
            ),
            Requirement(
                "No unresolved disagreements between reviewers",
                not any(status.state == "conflict" for _, status in statuses),
            ),
        ]
        sample = db.scalar(
            select(models.QASample)
            .where(models.QASample.project_id == project.id, models.QASample.stage == TITLE_ABSTRACT)
            .order_by(models.QASample.id.desc())
            .limit(1)
        )
        if sample is not None:
            by_id = {r.id: r for r in unique}
            screening_requirements.append(
                Requirement(
                    "Every record in the quality-assurance sample screened",
                    all(policy.final(by_id[i]) in ("include", "exclude") for i in sample.record_ids if i in by_id),
                )
            )
        return screening_requirements
    if stage == "full_text_screening":
        full_text_statuses = [policy.status(r, FULL_TEXT) for r in unique if policy.sought(r)]
        return [
            Requirement(
                "Every report sought has a full-text decision (included, excluded with a reason, or not retrieved)",
                all(status.final in ("include", "exclude", "not_retrieved") for status in full_text_statuses),
            ),
            Requirement(
                "No unresolved disagreements between reviewers",
                not any(s.state == "conflict" for s in full_text_statuses),
            ),
            Requirement(
                "Every excluded report has an exclusion reason",
                all(status.reason_code for status in full_text_statuses if status.final == "exclude"),
            ),
        ]
    if stage == "extraction":
        progress = extraction_progress(db, project.id)
        return [
            Requirement("At least one included study", progress.studies > 0),
            Requirement("Every included report belongs to a study", progress.unlinked_reports == 0),
            Requirement(
                "Every required field has a final value for every included study (and arm)",
                progress.missing_required == 0,
            ),
            Requirement("No unresolved discrepancies between extractors", progress.discrepancies == 0),
            Requirement("Every imputed value approved by a second reviewer", progress.unapproved_imputations == 0),
        ]

    raise WorkflowError(f"No requirements are defined for {stage}", status_code=500)


def _snapshot_content(db: Session, project: models.Project, stage: str) -> dict[str, Any]:
    if stage == "protocol":
        protocol = project.protocol
        criteria = db.scalars(
            select(models.Criterion).where(models.Criterion.project_id == project.id).order_by(models.Criterion.id)
        )
        strategies = db.scalars(
            select(models.SearchStrategy)
            .where(models.SearchStrategy.project_id == project.id)
            .order_by(models.SearchStrategy.id)
        )
        sections = project_sections(db, project.id)
        return {
            "protocol": {name: getattr(protocol, name) for name in PROTOCOL_FIELDS} if protocol else None,
            "criteria": [
                {"kind": c.kind, "text": c.text, "status": c.status, "element": c.element, "source": c.source}
                for c in criteria
            ],
            "sections": {
                section.key: sections[section.key].content
                for section in PROTOCOL_SECTIONS
                if section.key in sections and sections[section.key].content
            },
            "search_strategies": [{"database": s.database, "query": s.query} for s in strategies],
            "extraction_fields": [field.name for field in project.extraction_fields],
        }
    if stage in EVIDENCE_STAGES:
        return _evidence_snapshot(db, project, stage)

    records = _load_records(db, project.id)
    if stage == "search":
        runs = db.scalars(
            select(models.SearchRun).where(models.SearchRun.project_id == project.id).order_by(models.SearchRun.id)
        )
        strategies = db.scalars(
            select(models.SearchStrategy)
            .where(models.SearchStrategy.project_id == project.id)
            .order_by(models.SearchStrategy.id)
        )
        return {
            "strategies": [{"database": s.database, "query": s.query, "version": s.version} for s in strategies],
            "press": press_status(db, project.id),
            "runs": [
                {
                    "kind": run.kind,
                    "database": run.database,
                    "source": run.source_label,
                    "query": run.query,
                    "result_count": run.result_count,
                    "total_available": run.total_available,
                    "connector": run.connector,
                    "interface": run.interface,
                    "searched_on": run.searched_on,
                    "file_format": run.file_format,
                    "filters": run.filters,
                    "executed_at": run.executed_at,
                }
                for run in runs
            ],
            "records": [
                {"id": r.id, "title": r.title, "doi": r.doi, "duplicate_of_id": r.duplicate_of_id} for r in records
            ],
        }
    policy = review_policy(db, project.id)
    if stage in ("screening", "full_text_screening"):
        screening_stage = TITLE_ABSTRACT if stage == "screening" else FULL_TEXT
        unique = [r for r in records if r.duplicate_of_id is None]
        content_records = []
        for record in unique:
            if screening_stage == FULL_TEXT and not policy.sought(record):
                continue
            status = policy.status(record, screening_stage)
            adjudication = next((a for a in record.adjudications if a.stage == screening_stage), None)
            content_records.append(
                {
                    "record_id": record.id,
                    "final_decision": status.final,
                    "reason_code": status.reason_code,
                    "state": status.state,
                    "reviewer_decisions": _decisions_out(record, screening_stage),
                    "adjudication": {
                        "decision": adjudication.decision,
                        "reason_code": adjudication.reason_code,
                        "rationale": adjudication.rationale,
                        "adjudicator_id": adjudication.adjudicator_id,
                    }
                    if adjudication
                    else None,
                }
            )
        snapshot: dict[str, Any] = {
            "reviewers_per_record": policy.reviewers(screening_stage),
            "records": content_records,
        }
        if stage == "screening":
            stop = accepted_stopping(db, project.id, TITLE_ABSTRACT, len(unique))
            snapshot["stopping_rule"] = (
                {
                    "evaluation_id": stop.id,
                    "method": stop.method,
                    "result": stop.result,
                    "accepted_by_id": stop.accepted_by_id,
                    "rationale": stop.acceptance_rationale,
                }
                if stop
                else None
            )
        return snapshot
    if stage == "extraction":
        return dataset_content(db, project.id)

    raise WorkflowError(f"No snapshot is defined for {stage}", status_code=500)


EVIDENCE_STAGES = ("appraisal", "synthesis", "certainty")


def _final_run(analysis: models.Analysis) -> models.AnalysisRun | None:
    current = hashlib.sha256(_canonical(analysis.spec).encode()).hexdigest()
    runs = reversed(analysis.runs)
    return next((r for r in runs if r.is_final and r.status == "succeeded" and r.spec_sha256 == current), None)


def _project_rows[ModelT: models.Base](db: Session, model: type[ModelT], project_id: int) -> list[ModelT]:
    return list(db.scalars(select(model).where(model.project_id == project_id).order_by(model.id)))  # type: ignore[attr-defined]


def _evidence_requirements(db: Session, project: models.Project, stage: str) -> list[Requirement]:
    if stage == "appraisal":
        studies = included_studies(db, project.id)
        assessments = _project_rows(db, models.AppraisalAssessment, project.id)
        assessed = {a.study_id for a in assessments}
        return [
            Requirement(
                "Every included study has a risk of bias or quality assessment",
                bool(studies) and all(study.id in assessed for study in studies),
            ),
            Requirement(
                "Every assessment signed off, with each domain judged and justified",
                all(a.status == "signed_off" for a in assessments),
            ),
        ]
    analyses = _project_rows(db, models.Analysis, project.id)
    finished = [a for a in analyses if a.status == "approved" and _final_run(a) is not None]
    if stage == "synthesis":
        plan = (project.protocol.analysis_plan if project.protocol else None) or {}
        primary = [
            o["name"].casefold() for o in plan.get("outcomes", []) if o.get("priority") == "primary" and o.get("name")
        ]
        analysed = {a.outcome.casefold() for a in finished}
        return [
            Requirement(
                "Every analysis approved by a statistician",
                bool(analyses) and all(a.status == "approved" for a in analyses),
            ),
            Requirement(
                "Every primary outcome in the analysis plan has an approved analysis with final results",
                all(name in analysed for name in primary) if primary else bool(finished),
            ),
            Requirement(
                "Every approved analysis has final results (run in R on the locked extraction data set)",
                all(_final_run(a) is not None for a in analyses if a.status == "approved"),
            ),
        ]
    grades = _project_rows(db, models.GradeAssessment, project.id)
    graded = {g.outcome.casefold() for g in grades}
    return [
        Requirement(
            "Every analysed outcome has a GRADE assessment",
            bool(grades) and all(a.outcome.casefold() in graded for a in finished),
        ),
        Requirement(
            "Every GRADE assessment signed off", bool(grades) and all(g.status == "signed_off" for g in grades)
        ),
        Requirement(
            "Every Evidence to Decision framework signed off",
            all(e.status == "signed_off" for e in _project_rows(db, models.EtdFramework, project.id)),
        ),
        Requirement(
            "Every interpretive text approved by a clinical expert",
            all(t.status == "approved" for t in _project_rows(db, models.InterpretationText, project.id)),
        ),
    ]


def _evidence_snapshot(db: Session, project: models.Project, stage: str) -> dict[str, Any]:
    if stage == "appraisal":
        return {
            "assessments": [
                {
                    "id": a.id,
                    "study_id": a.study_id,
                    "tool": a.tool,
                    "tool_version": a.tool_version,
                    "outcome": a.outcome,
                    "selection_reason": a.selection_reason,
                    "answers": {answer.question_id: answer.answer for answer in a.answers},
                    "domains": {
                        d.domain: {
                            "judgment": d.judgment,
                            "rationale": d.rationale,
                            "algorithm_judgment": d.algorithm_judgment,
                            "signed_off_by_id": d.signed_off_by_id,
                        }
                        for d in a.domains
                    },
                    "overall_judgment": a.overall_judgment,
                    "overall_rationale": a.overall_rationale,
                    "signed_off_by_id": a.signed_off_by_id,
                }
                for a in _project_rows(db, models.AppraisalAssessment, project.id)
            ],
            "reporting": [
                {
                    "study_id": r.study_id,
                    "checklist": r.checklist,
                    "status": r.status,
                    "items": {item.item_id: item.status for item in r.items},
                }
                for r in _project_rows(db, models.ReportingAssessment, project.id)
            ],
        }
    if stage == "synthesis":
        analyses = []
        for analysis in _project_rows(db, models.Analysis, project.id):
            run = _final_run(analysis)
            analyses.append(
                {
                    "id": analysis.id,
                    "title": analysis.title,
                    "outcome": analysis.outcome,
                    "analysis_type": analysis.analysis_type,
                    "spec": analysis.spec,
                    "prespecified": analysis.prespecified,
                    "plan_reference": analysis.plan_reference,
                    "justification": analysis.justification,
                    "approved_by_id": analysis.approved_by_id,
                    "approval_note": analysis.approval_note,
                    "final_run": {
                        "id": run.id,
                        "spec_sha256": run.spec_sha256,
                        "dataset_sha256": run.dataset_sha256,
                        "extraction_snapshot_id": run.extraction_snapshot_id,
                        "seed": run.seed,
                        "script_sha256": hashlib.sha256(run.script.encode()).hexdigest(),
                        "r_version": run.r_version,
                        "packages": run.packages,
                        "results": run.results,
                    }
                    if run
                    else None,
                }
            )
        return {"analyses": analyses}
    # The summary of findings is computed by the certainty module, which itself depends on this one.
    from certainty_routes import summary_of_findings

    return {
        "grade_assessments": [
            {
                "id": g.id,
                "outcome": g.outcome,
                "comparison": g.comparison,
                "analysis_id": g.analysis_id,
                "importance": g.importance,
                "starting_certainty": g.starting_certainty,
                "domains": g.domains,
                "certainty": g.certainty,
                "mid": g.mid,
                "mid_scale": g.mid_scale,
                "baseline_risks": g.baseline_risks,
                "signed_off_by_id": g.signed_off_by_id,
            }
            for g in _project_rows(db, models.GradeAssessment, project.id)
        ],
        "summary_of_findings": summary_of_findings(db, project),
        "evidence_to_decision": [
            {"id": e.id, "title": e.title, "criteria": e.criteria, "conclusions": e.conclusions, "status": e.status}
            for e in _project_rows(db, models.EtdFramework, project.id)
        ],
        "prior_reviews": [
            {
                "id": r.id,
                "title": r.title,
                "doi": r.doi,
                "outcome": r.outcome,
                "conclusion_direction": r.conclusion_direction,
            }
            for r in _project_rows(db, models.PriorReview, project.id)
        ],
        "interpretation": [
            {"id": t.id, "kind": t.kind, "outcome": t.outcome, "content": t.content, "approved_by_id": t.approved_by_id}
            for t in _project_rows(db, models.InterpretationText, project.id)
        ],
    }


def _canonical(content: dict[str, Any]) -> str:
    return json.dumps(content, sort_keys=True, default=str)


def snapshot_is_intact(snapshot: models.StageSnapshot) -> bool:
    return hashlib.sha256(_canonical(snapshot.content).encode()).hexdigest() == snapshot.sha256


def _latest_version(db: Session, project_id: int, stage: str) -> int:
    return (
        db.scalar(
            select(func.max(models.StageSnapshot.version)).where(
                models.StageSnapshot.project_id == project_id, models.StageSnapshot.stage == stage
            )
        )
        or 0
    )


def complete_stage(
    db: Session, project: models.Project, stage: str, actor: models.User, note: str
) -> models.StageSnapshot:
    """Sign off a stage: check its requirements, snapshot its content, and open the next stage."""
    require_stage_open(db, project.id, stage)
    unmet = [requirement.label for requirement in requirements(db, project, stage) if not requirement.met]
    if unmet:
        raise WorkflowError(f"{STAGE_LABELS[stage]} isn't ready to complete: {'; '.join(unmet)}.")

    if stage == "full_text_screening":
        # Every included report starts as its own study; reviewers link reports of the same study during extraction.
        ensure_studies(db, project.id, included_records(db, project.id, review_policy(db, project.id)), actor.id)
    row = _stage_rows(db, project.id).get(stage) or models.ProjectStage(project_id=project.id, stage=stage)
    content = _snapshot_content(db, project, stage)
    if stage == "protocol":
        if row.reopen_rationale:
            content["amendment_rationale"] = row.reopen_rationale
    else:
        content["protocol_version"] = _latest_version(db, project.id, "protocol")
    canonical = _canonical(content)
    snapshot = models.StageSnapshot(
        project_id=project.id,
        stage=stage,
        version=_latest_version(db, project.id, stage) + 1,
        content=json.loads(canonical),
        sha256=hashlib.sha256(canonical.encode()).hexdigest(),
        note=note,
        created_by_id=actor.id,
    )

    row.completed_by_id, row.completed_at, row.completion_note = actor.id, models.utcnow(), note
    row.reopened_by_id, row.reopened_at, row.reopen_rationale = None, None, None
    db.add_all([row, snapshot])
    db.flush()
    record_event(
        db,
        project_id=project.id,
        actor_id=actor.id,
        action="stage.completed",
        entity_type="stage",
        entity_id=stage,
        details={"version": snapshot.version, "sha256": snapshot.sha256, "note": note},
    )
    return snapshot


def reopen_stage(db: Session, project: models.Project, stage: str, actor: models.User, rationale: str) -> list[str]:
    """Reopen a completed stage and every completed stage after it. Returns the stages reopened."""
    _check_stage_name(stage)
    rows = _stage_rows(db, project.id)
    if not _is_completed(rows, stage):
        raise WorkflowError(f"{STAGE_LABELS[stage]} is not completed, so there is nothing to reopen.")

    reopened = [name for name in STAGES[STAGES.index(stage) :] if _is_completed(rows, name)]
    now = models.utcnow()
    for name in reopened:
        row = rows[name]
        row.completed_by_id, row.completed_at, row.completion_note = None, None, None
        row.reopened_by_id, row.reopened_at = actor.id, now
        row.reopen_rationale = rationale if name == stage else f"Reopened with {STAGE_LABELS[stage]}: {rationale}"
    db.flush()
    record_event(
        db,
        project_id=project.id,
        actor_id=actor.id,
        action="stage.reopened",
        entity_type="stage",
        entity_id=stage,
        details={"rationale": rationale, "stages_reopened": reopened},
    )
    return reopened


def workflow_overview(db: Session, project: models.Project, can: Callable[[Permission], bool]) -> list[dict[str, Any]]:
    rows = _stage_rows(db, project.id)
    latest: dict[str, models.StageSnapshot] = {}
    snapshots = db.scalars(
        select(models.StageSnapshot)
        .where(models.StageSnapshot.project_id == project.id)
        .order_by(models.StageSnapshot.id)
    )
    for item in snapshots:
        latest[item.stage] = item

    overview = []
    for stage in STAGES:
        row, status, snapshot = rows.get(stage), _status(rows, stage), latest.get(stage)
        overview.append(
            {
                "stage": stage,
                "label": STAGE_LABELS[stage],
                "status": status,
                "requirements": [asdict(r) for r in requirements(db, project, stage)] if status == "open" else [],
                "completed_at": row.completed_at if row else None,
                "completed_by": row.completed_by.full_name if row and row.completed_by else None,
                "completion_note": row.completion_note if row else None,
                "reopen_rationale": row.reopen_rationale if row else None,
                "latest_version": snapshot.version if snapshot else None,
                "latest_snapshot_id": snapshot.id if snapshot else None,
                "can_manage": can(STAGE_PERMISSIONS[stage]),
            }
        )
    return overview
