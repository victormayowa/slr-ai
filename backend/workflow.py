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
from permissions import Permission
from protocol_design import project_sections, protocol_issues
from protocol_frameworks import PROTOCOL_SECTIONS, REQUIRED_SECTIONS
from review_data import TITLE_ABSTRACT, final_decision, find_duplicates, has_successful_run, latest_run

STAGES = ["protocol", "search", "screening", "extraction", "appraisal", "synthesis"]

STAGE_LABELS = {
    "protocol": "Protocol",
    "search": "Search and deduplication",
    "screening": "Title and abstract screening",
    "extraction": "Data extraction",
    "appraisal": "Risk of bias assessment",
    "synthesis": "Synthesis",
}

# Who may complete or reopen each stage.
STAGE_PERMISSIONS = {
    "protocol": Permission.APPROVE_PROTOCOL,
    "search": Permission.MANAGE_WORKFLOW,
    "screening": Permission.MANAGE_WORKFLOW,
    "extraction": Permission.MANAGE_WORKFLOW,
    "appraisal": Permission.MANAGE_WORKFLOW,
    "synthesis": Permission.APPROVE_ANALYSIS,
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
                selectinload(models.Record.ai_runs).selectinload(models.AIRun.appraisal),
                selectinload(models.Record.ai_runs)
                .selectinload(models.AIRun.extraction_values)
                .selectinload(models.ExtractionSuggestion.field),
            )
            .order_by(models.Record.id)
        )
    )


def _included(records: list[models.Record]) -> list[models.Record]:
    return [r for r in records if r.duplicate_of_id is None and final_decision(r) == "include"]


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
    if stage == "synthesis":
        return [Requirement("A synthesis report generated", _count(db, models.SynthesisReport, project.id) > 0)]

    records = _load_records(db, project.id)
    if stage == "search":
        registrations = db.scalars(
            select(models.ProtocolRegistration).where(models.ProtocolRegistration.project_id == project.id)
        ).all()
        recorded = [r for r in registrations if r.status in ("submitted", "registered", "waived")]
        search_requirements = [
            Requirement("At least one database search or file import", _count(db, models.SearchRun, project.id) > 0),
            Requirement("Deduplication run on every record", not find_duplicates(records)),
            Requirement("Protocol registration submitted, or registration waived with a reason", bool(recorded)),
        ]
        latest_version = _latest_version(db, project.id, "protocol")
        if any(r.status != "waived" and r.protocol_version < latest_version for r in recorded):
            search_requirements.append(
                Requirement(f"Registry record updated with protocol version {latest_version}", False)
            )
        return search_requirements
    if stage == "screening":
        unique = [r for r in records if r.duplicate_of_id is None]
        return [
            Requirement(
                "Every record has an include or exclude decision",
                all(final_decision(r) in ("include", "exclude") for r in unique),
            )
        ]

    included = _included(records)
    if stage == "extraction":
        return [
            Requirement("At least one included record", bool(included)),
            Requirement(
                "Every included record has extraction results",
                all(has_successful_run(r, "extraction") for r in included),
            ),
        ]
    return [
        Requirement(
            "Every included record has a risk of bias assessment",
            all(has_successful_run(r, "appraisal") for r in included),
        )
    ]


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
    if stage == "synthesis":
        report = db.scalar(
            select(models.SynthesisReport)
            .where(models.SynthesisReport.project_id == project.id)
            .order_by(models.SynthesisReport.id.desc())
            .limit(1)
        )
        return (
            {"report_id": report.id, "record_count": report.record_count, "content": report.content} if report else {}
        )

    records = _load_records(db, project.id)
    if stage == "search":
        runs = db.scalars(
            select(models.SearchRun).where(models.SearchRun.project_id == project.id).order_by(models.SearchRun.id)
        )
        return {
            "runs": [
                {
                    "kind": run.kind,
                    "database": run.database,
                    "source": run.source_label,
                    "query": run.query,
                    "result_count": run.result_count,
                    "executed_at": run.executed_at,
                }
                for run in runs
            ],
            "records": [
                {"id": r.id, "title": r.title, "doi": r.doi, "duplicate_of_id": r.duplicate_of_id} for r in records
            ],
        }
    if stage == "screening":
        return {
            "records": [
                {
                    "record_id": r.id,
                    "final_decision": final_decision(r),
                    "reviewer_decisions": [
                        {"reviewer_id": d.reviewer_id, "decision": d.decision}
                        for d in r.decisions
                        if d.stage == TITLE_ABSTRACT
                    ],
                }
                for r in records
                if r.duplicate_of_id is None
            ]
        }

    content: list[dict[str, Any]] = []
    for record in _included(records):
        run = latest_run(record, stage if stage == "extraction" else "appraisal")
        if run is None:
            continue
        if stage == "extraction":
            content.append(
                {
                    "record_id": record.id,
                    "title": record.title,
                    "ai_run_id": run.id,
                    "values": {value.field.name: value.value for value in run.extraction_values},
                }
            )
        else:
            content.append(
                {
                    "record_id": record.id,
                    "ai_run_id": run.id,
                    "tool": run.appraisal.tool if run.appraisal else None,
                    "judgments": run.appraisal.judgments if run.appraisal else None,
                }
            )
    return {"records": content}


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
