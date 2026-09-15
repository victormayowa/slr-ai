"""The extraction dataset: included studies, the cells of the form (field × study × arm), each extractor's values, the
final values, and whether each cell is settled.

How a cell becomes final depends on the project's extraction mode (review_settings.ExtractionSettings):
- single: the extractor's value is final.
- dual: two extractors' values that agree (within the numeric tolerance) become final; if they disagree, a reviewer
  reconciles the cell with a rationale.
- human_and_ai: the extractor's value is compared with the AI's grounded suggestion; agreement makes it final, and
  disagreement is reconciled by a reviewer.
A reconciled value stays final until an extractor changes a value after the reconciliation.
"""

from collections import defaultdict
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import models
from extraction_values import display, values_agree
from review_data import ReviewPolicy
from review_settings import ReviewSettingsData, load_settings, review_policy
from studies import project_studies

SETTLED_STATES = ("final", "agreed", "reconciled")


def included_records(db: Session, project_id: int, policy: ReviewPolicy) -> list[models.Record]:
    records = db.scalars(
        select(models.Record)
        .where(models.Record.project_id == project_id, models.Record.duplicate_of_id.is_(None))
        .options(selectinload(models.Record.decisions), selectinload(models.Record.adjudications))
        .order_by(models.Record.id)
    ).all()
    return [record for record in records if policy.included(record)]


def included_studies(db: Session, project_id: int, policy: ReviewPolicy | None = None) -> list[models.Study]:
    """Studies with at least one report included in the review."""
    included = {record.id for record in included_records(db, project_id, policy or review_policy(db, project_id))}
    return [s for s in project_studies(db, project_id) if any(r.record_id in included for r in s.reports)]


def project_fields(db: Session, project_id: int) -> list[models.ExtractionField]:
    return list(
        db.scalars(
            select(models.ExtractionField)
            .where(models.ExtractionField.project_id == project_id)
            .order_by(models.ExtractionField.position, models.ExtractionField.id)
        )
    )


@dataclass
class Cell:
    study: models.Study
    field: models.ExtractionField
    arm: models.StudyArm | None
    values: list[models.ExtractionValue] = dataclass_field(default_factory=list)
    final: models.ExtractionFinal | None = None
    ai: models.ExtractionSuggestion | None = None
    state: str = "empty"

    @property
    def key(self) -> tuple[int, int, int | None]:
        return (self.study.id, self.field.id, self.arm.id if self.arm else None)


def latest_suggestions(db: Session, study_ids: list[int]) -> dict[int, list[models.ExtractionSuggestion]]:
    """Suggestions from each study's latest successful AI extraction."""
    runs: dict[int, int] = {}
    for run in db.scalars(
        select(models.AIRun)
        .where(
            models.AIRun.study_id.in_(study_ids),
            models.AIRun.task == "extraction",
            models.AIRun.status == "succeeded",
        )
        .order_by(models.AIRun.id)
    ):
        if run.study_id is not None:
            runs[run.study_id] = run.id
    by_study: dict[int, list[models.ExtractionSuggestion]] = defaultdict(list)
    if runs:
        for suggestion in db.scalars(
            select(models.ExtractionSuggestion)
            .where(models.ExtractionSuggestion.ai_run_id.in_(list(runs.values())))
            .order_by(models.ExtractionSuggestion.id)
        ):
            if suggestion.study_id is not None:
                by_study[suggestion.study_id].append(suggestion)
    return by_study


def _matching_suggestion(
    suggestions: list[models.ExtractionSuggestion], field_id: int, arm: models.StudyArm | None
) -> models.ExtractionSuggestion | None:
    label = (arm.label if arm else "").strip().casefold()
    return next(
        (s for s in suggestions if s.field_id == field_id and s.arm_label.strip().casefold() == label),
        None,
    )


def _entry(
    item: models.ExtractionValue | models.ExtractionFinal | models.ExtractionSuggestion,
) -> tuple[dict | None, bool]:
    if isinstance(item, models.ExtractionSuggestion):
        return item.structured, item.not_reported
    return item.value, item.not_reported


def cell_state(cell: Cell, settings: ReviewSettingsData) -> str:
    mode = settings.extraction.mode
    tolerance = (settings.extraction.numeric_absolute_tolerance, settings.extraction.numeric_relative_tolerance)
    if cell.field.per_arm and cell.arm is None:
        return "arms_missing"
    newest_value = max((v.updated_at for v in cell.values), default=None)
    if (
        cell.final is not None
        and cell.final.source == "reconciled"
        and (newest_value is None or cell.final.decided_at >= newest_value)
    ):
        return "reconciled"
    if mode == "single":
        return "final" if cell.values else "empty"
    if not cell.values:
        return "empty"
    if mode == "dual":
        if len(cell.values) < 2:
            return "awaiting_second_extractor"
        first = _entry(cell.values[0])
        agree = all(values_agree(cell.field, first, _entry(v), *tolerance) for v in cell.values[1:])
        return "agreed" if agree else "discrepancy"
    # human_and_ai
    usable_ai = cell.ai is not None and cell.ai.grounding in ("grounded", "not_reported")
    if not usable_ai:
        return "awaiting_ai" if cell.ai is None else "discrepancy"
    assert cell.ai is not None
    human = _entry(cell.values[0])
    return "agreed" if values_agree(cell.field, human, _entry(cell.ai), *tolerance) else "discrepancy"


def build_cells(
    db: Session,
    studies: list[models.Study],
    fields: list[models.ExtractionField],
    settings: ReviewSettingsData,
) -> list[Cell]:
    study_ids = [study.id for study in studies]
    values: dict[tuple[int, int, int | None], list[models.ExtractionValue]] = defaultdict(list)
    for value in db.scalars(
        select(models.ExtractionValue)
        .where(models.ExtractionValue.study_id.in_(study_ids))
        .options(selectinload(models.ExtractionValue.extractor))
        .order_by(models.ExtractionValue.created_at, models.ExtractionValue.id)
    ):
        values[(value.study_id, value.field_id, value.arm_id)].append(value)
    finals = {
        (final.study_id, final.field_id, final.arm_id): final
        for final in db.scalars(select(models.ExtractionFinal).where(models.ExtractionFinal.study_id.in_(study_ids)))
    }
    suggestions = latest_suggestions(db, study_ids)
    cells = []
    for study in studies:
        for item in fields:
            arms: list[models.StudyArm | None] = list(study.arms) if item.per_arm and study.arms else [None]
            for arm in arms:
                key = (study.id, item.id, arm.id if arm else None)
                cell = Cell(
                    study,
                    item,
                    arm,
                    values.get(key, []),
                    finals.get(key),
                    _matching_suggestion(suggestions.get(study.id, []), item.id, arm),
                )
                cell.state = cell_state(cell, settings)
                cells.append(cell)
    return cells


def _copy(target: models.ExtractionFinal, source: models.ExtractionValue) -> None:
    target.value, target.not_reported, target.unit = source.value, source.not_reported, source.unit
    target.span_ids, target.flags, target.derivation = list(source.span_ids), list(source.flags), source.derivation


def final_snapshot(final: models.ExtractionFinal | None) -> dict[str, Any] | None:
    if final is None:
        return None
    return {
        "value": final.value,
        "not_reported": final.not_reported,
        "unit": final.unit,
        "source": final.source,
        "flags": final.flags,
    }


def refresh_final(db: Session, cell: Cell, settings: ReviewSettingsData, actor_id: int | None) -> None:
    """Make the cell's automatic final value (single or agreed) match its current values. Reconciled values are kept."""
    state = cell_state(cell, settings)
    final = cell.final
    before = final_snapshot(final)
    if state == "reconciled":
        return
    source_value: models.ExtractionValue | None = None
    if state == "final":
        source_value = max(cell.values, key=lambda v: v.updated_at)
        source = "single"
    elif state == "agreed":
        source_value = cell.values[0]
        source = "agreed"
    if source_value is not None:
        if final is None:
            final = models.ExtractionFinal(
                project_id=cell.study.project_id,
                study_id=cell.study.id,
                field_id=cell.field.id,
                arm_id=cell.arm.id if cell.arm else None,
                source=source,
            )
            db.add(final)
            cell.final = final
        _copy(final, source_value)
        final.source, final.decided_by_id, final.decided_at, final.rationale = source, actor_id, models.utcnow(), ""
    elif final is not None and final.source in ("single", "agreed", "reconciled"):
        db.delete(final)
        cell.final = None
    after = final_snapshot(cell.final)
    if before != after:
        db.add(
            models.ExtractionValueRevision(
                project_id=cell.study.project_id,
                study_id=cell.study.id,
                field_id=cell.field.id,
                arm_id=cell.arm.id if cell.arm else None,
                kind="final",
                previous=before,
                new=after,
                reason=f"Automatic: {state.replace('_', ' ')}",
                changed_by_id=actor_id,
            )
        )
    cell.state = cell_state(cell, settings)


@dataclass
class ExtractionProgress:
    studies: int
    unlinked_reports: int
    cells: int
    settled: int
    missing_required: int
    discrepancies: int
    awaiting: int
    unapproved_imputations: int


def extraction_progress(db: Session, project_id: int) -> ExtractionProgress:
    settings = load_settings(db, project_id)
    policy = ReviewPolicy(settings.screening.title_abstract_reviewers, settings.screening.full_text_reviewers)
    records = included_records(db, project_id, policy)
    linked = set(
        db.scalars(
            select(models.StudyReport.record_id).where(models.StudyReport.record_id.in_([r.id for r in records]))
        )
    )
    studies = included_studies(db, project_id, policy)
    cells = build_cells(db, studies, project_fields(db, project_id), settings)
    unapproved = db.scalar(
        select(models.ExtractionValue.id)
        .where(
            models.ExtractionValue.project_id == project_id,
            models.ExtractionValue.flags.contains(["imputed"]),
            models.ExtractionValue.approved_by_id.is_(None),
        )
        .limit(1)
    )
    return ExtractionProgress(
        studies=len(studies),
        unlinked_reports=sum(1 for r in records if r.id not in linked),
        cells=len(cells),
        settled=sum(1 for c in cells if c.state in SETTLED_STATES),
        missing_required=sum(1 for c in cells if c.field.required and c.state not in SETTLED_STATES),
        discrepancies=sum(1 for c in cells if c.state == "discrepancy"),
        awaiting=sum(1 for c in cells if c.state in ("awaiting_second_extractor", "awaiting_ai")),
        unapproved_imputations=0 if unapproved is None else 1,
    )


def field_out(item: models.ExtractionField) -> dict[str, Any]:
    return {
        "id": item.id,
        "name": item.name,
        "position": item.position,
        "section": item.section,
        "field_type": item.field_type,
        "options": item.options,
        "unit": item.unit,
        "required": item.required,
        "help_text": item.help_text,
        "per_arm": item.per_arm,
        "outcome": item.outcome,
        "timepoint": item.timepoint,
        "settings": item.settings,
    }


def dataset_content(db: Session, project_id: int) -> dict[str, Any]:
    """The final dataset, as locked when extraction is signed off and as exported."""
    settings = load_settings(db, project_id)
    studies = included_studies(db, project_id)
    fields = project_fields(db, project_id)
    cells = build_cells(db, studies, fields, settings)
    by_study: dict[int, list[Cell]] = defaultdict(list)
    for cell in cells:
        by_study[cell.study.id].append(cell)
    return {
        "extraction_mode": settings.extraction.mode,
        "fields": [field_out(item) for item in fields],
        "studies": [
            {
                "study_id": study.id,
                "label": study.label,
                "registry_ids": study.registry_ids,
                "reports": [
                    {
                        "record_id": report.record_id,
                        "title": report.record.title,
                        "doi": report.record.doi,
                        "is_primary": report.is_primary,
                    }
                    for report in study.reports
                ],
                "arms": [{"arm_id": arm.id, "label": arm.label, "description": arm.description} for arm in study.arms],
                "values": [
                    {
                        "field_id": cell.field.id,
                        "field": cell.field.name,
                        "arm_id": cell.arm.id if cell.arm else None,
                        "arm": cell.arm.label if cell.arm else None,
                        "state": cell.state,
                        "value": cell.final.value if cell.final else None,
                        "display": display(cell.field, cell.final.value, cell.final.not_reported) if cell.final else "",
                        "not_reported": cell.final.not_reported if cell.final else False,
                        "unit": cell.final.unit if cell.final else "",
                        "source": cell.final.source if cell.final else None,
                        "flags": cell.final.flags if cell.final else [],
                        "derivation": cell.final.derivation if cell.final else None,
                        "span_ids": cell.final.span_ids if cell.final else [],
                    }
                    for cell in by_study[study.id]
                ],
            }
            for study in studies
        ],
    }
