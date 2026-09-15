"""Data extraction: the extraction form, each extractor's values per study, AI suggestions, reconciliation, statistical
conversions, unit normalization, imputation, author contacts, cell-level history, and dataset export.

Values are entered per study (and per arm for arm-level fields) and become final according to the project's
extraction mode (extraction_data.py). Signing off the extraction stage locks the final dataset in a hashed snapshot.
"""

import math
from dataclasses import asdict
from datetime import UTC, date, datetime
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import models
import units
from audit import record_event
from database import get_db
from dataset_export import bundle, to_csv, to_json, to_xlsx
from documents import best_full_texts
from extraction_data import (
    Cell,
    build_cells,
    dataset_content,
    extraction_progress,
    field_out,
    final_snapshot,
    included_studies,
    project_fields,
    refresh_final,
)
from extraction_templates import TEMPLATES, outcome_fields
from extraction_templates import catalog as template_catalog
from extraction_values import FIELD_TYPES, InvalidValue, display, missing_components, validate
from permissions import Permission, has_permission
from projects_routes import ProjectAccess, get_in_project, project_access
from review_settings import ReviewSettingsData, load_settings
from stats_convert import ConversionError, convert
from stats_convert import catalog as conversion_catalog
from workflow import WorkflowError, require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["extraction"])

MAX_FIELDS = 200
# Value components that unit conversion leaves alone, because they're counts or have no unit.
_UNITLESS = {"n", "events", "total", "tp", "fp", "fn", "tn", "p_value", "measure"}
# Spread components change by the conversion's scale only, not its offset (for example °C to °F).
_SPREAD = {"sd", "se"}
_FIELD_SETTINGS = {"analyte", "direction", "decimals"}


# --- The form ---


class FieldIn(BaseModel):
    id: int | None = None
    name: str = Field(min_length=1, max_length=200)
    section: str = Field("General", min_length=1, max_length=100)
    field_type: str = Field(max_length=30)
    options: list[str] = Field(default_factory=list, max_length=100)
    unit: str = Field("", max_length=40)
    required: bool = False
    help_text: str = Field("", max_length=2000)
    per_arm: bool = False
    outcome: str = Field("", max_length=200)
    timepoint: str = Field("", max_length=100)
    settings: dict[str, Any] = Field(default_factory=dict)


class FormUpdate(BaseModel):
    fields: list[FieldIn] = Field(max_length=MAX_FIELDS)
    # Required once the protocol is locked: why the form changed.
    note: str | None = Field(None, max_length=2000)


class TemplateRequest(BaseModel):
    template: str = Field(max_length=40)
    note: str | None = Field(None, max_length=2000)


def _form_change_note(db: Session, project_id: int, note: str | None) -> tuple[str | None, bool]:
    """The form changes freely while the protocol is open. Once it's locked, it changes during screening or extraction
    with a note, and the change is reported as an amendment. Returns (note, whether the protocol is locked)."""
    try:
        require_stage_open(db, project_id, "protocol")
        return (note or "").strip() or None, False
    except WorkflowError:
        pass
    for stage in ("screening", "full_text_screening", "extraction"):
        try:
            require_stage_open(db, project_id, stage)
            break
        except WorkflowError:
            continue
    else:
        raise WorkflowError("The extraction form can change while the protocol, screening, or extraction is open.")
    if not note or len(note.strip()) < 10:
        raise HTTPException(
            status_code=422,
            detail="The protocol is locked, so explain why the extraction form changed (at least 10 characters)",
        )
    return note.strip(), True


def _check_field(item: FieldIn) -> None:
    if item.field_type not in FIELD_TYPES:
        raise HTTPException(status_code=422, detail=f"Unknown field type: {item.field_type}")
    options = [option.strip() for option in item.options if option.strip()]
    if item.field_type in ("categorical", "multi_select") and len(set(options)) < 2:
        raise HTTPException(status_code=422, detail=f"{item.name} needs at least two different options")
    unknown = set(item.settings) - _FIELD_SETTINGS
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown field settings: {', '.join(sorted(unknown))}")
    analyte = item.settings.get("analyte")
    if analyte is not None and analyte not in units.ANALYTES and analyte != "hba1c":
        raise HTTPException(status_code=422, detail=f"Unknown analyte: {analyte}")
    if item.settings.get("direction") not in (None, "higher_is_better", "lower_is_better"):
        raise HTTPException(status_code=422, detail="direction must be higher_is_better or lower_is_better")


def _field_has_data(db: Session, field_id: int) -> bool:
    return any(
        db.scalar(select(model.id).where(model.field_id == field_id).limit(1)) is not None
        for model in (models.ExtractionValue, models.ExtractionFinal)
    )


def _form(db: Session, access: ProjectAccess) -> dict:
    protocol = access.project.protocol
    outcomes = (protocol.analysis_plan or {}).get("outcomes", []) if protocol else []
    return {
        "fields": [field_out(item) for item in project_fields(db, access.project.id)],
        "field_types": [
            {"key": key, "label": kind.label, "components": list(kind.components)} for key, kind in FIELD_TYPES.items()
        ],
        "templates": template_catalog(),
        "conversions": conversion_catalog(),
        "units": units.catalog(),
        "settings": load_settings(db, access.project.id).extraction.model_dump(),
        "analysis_outcomes": [str(o.get("name")) for o in outcomes if o.get("name")],
    }


@router.get("/extraction/form")
def get_form(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)):
    return _form(db, access)


@router.put("/extraction/form")
def update_form(
    body: FormUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    """Replace the extraction form. Fields with extracted values can't be removed or change type."""
    note, locked = _form_change_note(db, access.project.id, body.note)
    project = access.project
    existing = {item.id: item for item in project.extraction_fields}
    names = [item.name.strip() for item in body.fields]
    if len({name.casefold() for name in names}) != len(names):
        raise HTTPException(status_code=422, detail="Each field needs a different name")
    for item in body.fields:
        _check_field(item)
        if item.id is not None and item.id not in existing:
            raise HTTPException(status_code=404, detail=f"Field {item.id} wasn't found")
        if item.id is not None and existing[item.id].field_type != item.field_type and _field_has_data(db, item.id):
            raise HTTPException(
                status_code=409, detail=f"{existing[item.id].name} has extracted values, so its type can't change"
            )
    kept = {item.id for item in body.fields if item.id is not None}
    removed = [item for field_id, item in existing.items() if field_id not in kept]
    for stale in removed:
        if _field_has_data(db, stale.id):
            raise HTTPException(status_code=409, detail=f"{stale.name} has extracted values and can't be removed")
    for stale in removed:
        project.extraction_fields.remove(stale)
    db.flush()

    added, changed = [], []
    for position, item in enumerate(body.fields):
        attributes = {
            "name": item.name.strip(),
            "section": item.section.strip(),
            "field_type": item.field_type,
            "options": list(dict.fromkeys(o.strip() for o in item.options if o.strip())),
            "unit": item.unit.strip(),
            "required": item.required,
            "help_text": item.help_text.strip(),
            "per_arm": item.per_arm,
            "outcome": item.outcome.strip(),
            "timepoint": item.timepoint.strip(),
            "settings": item.settings,
            "position": position,
        }
        target = existing.get(item.id) if item.id is not None else None
        if target is None:
            project.extraction_fields.append(models.ExtractionField(**attributes))
            added.append(attributes["name"])
        else:
            if any(getattr(target, key) != value for key, value in attributes.items() if key != "position"):
                changed.append(attributes["name"])
            for key, value in attributes.items():
                setattr(target, key, value)
    db.flush()
    if added or changed or removed:
        record_event(
            db,
            project_id=project.id,
            actor_id=access.user.id,
            action="extraction_form.amended_after_protocol_lock" if locked else "extraction_form.updated",
            entity_type="project",
            entity_id=project.id,
            details={"added": added, "changed": changed, "removed": [stale.name for stale in removed], "note": note},
        )
    db.commit()
    return _form(db, access)


@router.post("/extraction/form/templates")
def apply_template(
    body: TemplateRequest,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    """Add a template's fields (or a field per pre-specified outcome) that the form doesn't have yet."""
    note, locked = _form_change_note(db, access.project.id, body.note)
    project = access.project
    if body.template == "outcomes":
        specs = outcome_fields(project.protocol.analysis_plan if project.protocol else {})
        if not specs:
            raise HTTPException(status_code=400, detail="The analysis plan has no pre-specified outcomes")
    elif body.template in TEMPLATES:
        specs = list(TEMPLATES[body.template].fields)
    else:
        raise HTTPException(status_code=404, detail=f"Unknown template: {body.template}")
    existing = {item.name.casefold() for item in project.extraction_fields}
    added = []
    for spec in specs:
        if spec.name.casefold() in existing:
            continue
        project.extraction_fields.append(
            models.ExtractionField(
                name=spec.name,
                section=spec.section,
                field_type=spec.field_type,
                per_arm=spec.per_arm,
                required=spec.required,
                options=list(spec.options),
                unit=spec.unit,
                help_text=spec.help_text,
                outcome=spec.outcome,
                timepoint=spec.timepoint,
                position=len(project.extraction_fields),
            )
        )
        existing.add(spec.name.casefold())
        added.append(spec.name)
    if added:
        record_event(
            db,
            project_id=project.id,
            actor_id=access.user.id,
            action="extraction_form.amended_after_protocol_lock" if locked else "extraction_form.updated",
            entity_type="project",
            entity_id=project.id,
            details={"template": body.template, "added": added, "note": note},
        )
    db.commit()
    return _form(db, access)


# --- Values ---


def _study(db: Session, access: ProjectAccess, study_id: int) -> models.Study:
    return get_in_project(db, models.Study, study_id, access.project.id, "Study")


def value_out(value: models.ExtractionValue, field: models.ExtractionField) -> dict:
    return {
        "id": value.id,
        "extractor": value.extractor.full_name if value.extractor else None,
        "extractor_id": value.extractor_id,
        "value": value.value,
        "display": display(field, value.value, value.not_reported),
        "not_reported": value.not_reported,
        "unit": value.unit,
        "span_ids": value.span_ids,
        "quote": value.quote,
        "flags": value.flags,
        "derivation": value.derivation,
        "source": value.source,
        "note": value.note,
        "needs_approval": "imputed" in value.flags and value.approved_by_id is None,
        "approved_at": value.approved_at,
        "updated_at": value.updated_at,
    }


def suggestion_out(suggestion: models.ExtractionSuggestion, field: models.ExtractionField) -> dict:
    return {
        "id": suggestion.id,
        "value": suggestion.value,
        "structured": suggestion.structured,
        "display": display(field, suggestion.structured, suggestion.not_reported)
        if suggestion.structured or suggestion.not_reported
        else suggestion.value,
        "unit": suggestion.unit,
        "not_reported": suggestion.not_reported,
        "quote": suggestion.evidence_quote,
        "span_ids": suggestion.span_ids,
        "confidence": suggestion.confidence,
        "ambiguous": suggestion.ambiguous,
        "grounding": suggestion.grounding,
        "arm_label": suggestion.arm_label,
    }


def final_out(final: models.ExtractionFinal, field: models.ExtractionField) -> dict:
    return {
        "id": final.id,
        "value": final.value,
        "display": display(field, final.value, final.not_reported),
        "not_reported": final.not_reported,
        "unit": final.unit,
        "span_ids": final.span_ids,
        "flags": final.flags,
        "derivation": final.derivation,
        "source": final.source,
        "rationale": final.rationale,
        "decided_at": final.decided_at,
    }


def cell_out(cell: Cell, user_id: int, settings: ReviewSettingsData, can_reconcile: bool) -> dict:
    """A cell as this user may see it: in dual extraction, other extractors' values stay hidden until the user has
    entered their own; with AI as the second extractor, the AI suggestion stays hidden likewise."""
    mine = next((v for v in cell.values if v.extractor_id == user_id), None)
    others = [v for v in cell.values if v is not mine]
    mode = settings.extraction.mode
    reveal = can_reconcile or mine is not None or mode == "single"
    show_ai = mode != "human_and_ai" or mine is not None or can_reconcile
    basis = cell.final.value if cell.final else mine.value if mine else None
    return {
        "field_id": cell.field.id,
        "arm_id": cell.arm.id if cell.arm else None,
        "arm_label": cell.arm.label if cell.arm else None,
        "state": cell.state,
        "my_value": value_out(mine, cell.field) if mine else None,
        "other_values": [value_out(v, cell.field) for v in others] if reveal else None,
        "values_hidden": not reveal and bool(others),
        "final": final_out(cell.final, cell.field) if cell.final else None,
        "ai_suggestion": suggestion_out(cell.ai, cell.field) if cell.ai and show_ai else None,
        "ai_hidden": cell.ai is not None and not show_ai,
        "missing_components": missing_components(cell.field, basis),
    }


def _find_cell(
    db: Session, study: models.Study, field: models.ExtractionField, arm_id: int | None, settings: ReviewSettingsData
) -> Cell:
    cells = build_cells(db, [study], [field], settings)
    cell = next((c for c in cells if (c.arm.id if c.arm else None) == arm_id), None)
    if cell is None:
        raise HTTPException(status_code=404, detail="That arm wasn't found in this study")
    return cell


def _field_and_arm(
    db: Session, access: ProjectAccess, study: models.Study, field_id: int, arm_id: int | None
) -> models.ExtractionField:
    field = get_in_project(db, models.ExtractionField, field_id, access.project.id, "Field")
    if field.per_arm and arm_id is None:
        raise HTTPException(status_code=422, detail=f"{field.name} is extracted per arm: choose the arm")
    if not field.per_arm and arm_id is not None:
        raise HTTPException(status_code=422, detail=f"{field.name} is extracted once for the study, not per arm")
    if arm_id is not None and all(arm.id != arm_id for arm in study.arms):
        raise HTTPException(status_code=404, detail="That arm wasn't found in this study")
    return field


def _normalize_units(
    field: models.ExtractionField, value: dict[str, Any], unit: str
) -> tuple[dict[str, Any], str, dict | None]:
    """Convert a value entered in another unit to the field's unit, keeping the original as derivation."""
    unit = unit.strip()
    if not field.unit or not unit or units.normalize_unit(unit) == units.normalize_unit(field.unit):
        return value, unit or field.unit, None
    analyte = field.settings.get("analyte")
    converted: dict[str, Any] = {}
    method = ""
    try:
        for key, item in value.items():
            if isinstance(item, int | float) and not isinstance(item, bool) and key not in _UNITLESS:
                result = units.convert(float(item), unit, field.unit, analyte)
                if key in _SPREAD:
                    converted[key] = abs(result.value - units.convert(0.0, unit, field.unit, analyte).value)
                else:
                    converted[key] = result.value
                method = result.method
            else:
                converted[key] = item
    except units.UnitError as exc:
        raise HTTPException(status_code=422, detail=f"{exc}. Enter the value in {field.unit}.") from exc
    derivation = {
        "method": "unit_conversion",
        "from_unit": unit,
        "to_unit": field.unit,
        "conversion": method,
        "original": value,
    }
    return converted, field.unit, derivation


def _allowed_span_ids(db: Session, study: models.Study) -> set[int]:
    record_ids = [report.record_id for report in study.reports]
    return set(
        db.scalars(
            select(models.DocumentSpan.id)
            .join(models.Document, models.Document.id == models.DocumentSpan.document_id)
            .where(models.Document.record_id.in_(record_ids))
        )
    )


@router.get("/studies/{study_id}/extraction")
def study_extraction(
    study_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    study = _study(db, access, study_id)
    settings = load_settings(db, access.project.id)
    fields = project_fields(db, access.project.id)
    cells = build_cells(db, [study], fields, settings)
    can_reconcile = has_permission(access.membership.role, Permission.ADJUDICATE)
    documents = best_full_texts(db, [report.record_id for report in study.reports])
    return {
        "study": {
            "id": study.id,
            "label": study.label,
            "registry_ids": study.registry_ids,
            "arms": [{"id": arm.id, "label": arm.label, "description": arm.description} for arm in study.arms],
            "reports": [
                {
                    "record_id": report.record_id,
                    "title": report.record.title,
                    "doi": report.record.doi,
                    "is_primary": report.is_primary,
                    "document_id": documents[report.record_id].id if report.record_id in documents else None,
                    "file_name": documents[report.record_id].file_name if report.record_id in documents else None,
                }
                for report in study.reports
            ],
        },
        "fields": [field_out(item) for item in fields],
        "cells": [cell_out(cell, access.user.id, settings, can_reconcile) for cell in cells],
        "settings": settings.extraction.model_dump(),
        "can_reconcile": can_reconcile,
        "can_extract": has_permission(access.membership.role, Permission.EXTRACT),
    }


class ValueIn(BaseModel):
    field_id: int
    arm_id: int | None = None
    value: dict[str, Any] | None = None
    not_reported: bool = False
    unit: str = Field("", max_length=40)
    span_ids: list[int] = Field(default_factory=list, max_length=50)
    quote: str | None = Field(None, max_length=5000)
    note: str = Field("", max_length=5000)
    source: Literal["manual", "ai_accepted", "calculated", "imputed"] = "manual"
    ai_suggestion_id: int | None = None
    # How a calculated or imputed value was derived: {"method": ..., "inputs": ..., "formula": ..., "reference": ...}
    derivation: dict[str, Any] | None = None
    # Required when changing a value in a cell that already has a final value.
    reason: str | None = Field(None, max_length=2000)


def _value_state(value: models.ExtractionValue | None) -> dict | None:
    if value is None:
        return None
    return {"value": value.value, "not_reported": value.not_reported, "unit": value.unit, "flags": value.flags}


@router.put("/studies/{study_id}/extraction/values")
def save_value(
    study_id: int,
    body: ValueIn,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Save this extractor's value for a cell, with its evidence and provenance."""
    require_stage_open(db, access.project.id, "extraction")
    study = _study(db, access, study_id)
    field = _field_and_arm(db, access, study, body.field_id, body.arm_id)
    settings = load_settings(db, access.project.id)

    raw_value, not_reported, unit = body.value, body.not_reported, body.unit
    span_ids, quote = list(body.span_ids), body.quote
    flags: list[str] = []
    suggestion = None
    if body.source == "ai_accepted":
        suggestion = db.get(models.ExtractionSuggestion, body.ai_suggestion_id) if body.ai_suggestion_id else None
        if suggestion is None or suggestion.study_id != study.id or suggestion.field_id != field.id:
            raise HTTPException(status_code=404, detail="That AI suggestion wasn't found for this cell")
        if suggestion.grounding not in ("grounded", "not_reported"):
            raise HTTPException(
                status_code=409,
                detail="The AI's quote for this value wasn't found in the study's text, "
                "so the value can't be accepted. "
                "Check the report and enter the value yourself.",
            )
        if raw_value is None and not not_reported:
            raw_value, not_reported = suggestion.structured, suggestion.not_reported
            if raw_value is None and not not_reported:
                raise HTTPException(
                    status_code=422, detail="The suggestion doesn't fit this field's type; enter the value yourself"
                )
        span_ids = span_ids or list(suggestion.span_ids)
        quote = quote or suggestion.evidence_quote
        unit = unit or suggestion.unit
        if suggestion.ambiguous:
            flags.append("ambiguous")

    derivation = body.derivation
    value: dict[str, Any] | None = None
    if not not_reported:
        try:
            value = validate(field, raw_value or {})
        except InvalidValue as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        value, unit, unit_derivation = _normalize_units(field, value, unit)
        if unit_derivation is not None:
            flags.append("unit_converted")
            derivation = {**derivation, "unit_conversion": unit_derivation} if derivation else unit_derivation
    if body.source in ("calculated", "imputed"):
        if not body.derivation or not str(body.derivation.get("method") or "").strip():
            raise HTTPException(status_code=422, detail="Describe how the value was derived (method and inputs)")
        flags.append(body.source)
    if set(span_ids) - _allowed_span_ids(db, study):
        raise HTTPException(status_code=422, detail="Evidence must come from this study's documents")

    cell = _find_cell(db, study, field, body.arm_id, settings)
    mine = next((v for v in cell.values if v.extractor_id == access.user.id), None)
    previous = _value_state(mine)
    new_state = {"value": value, "not_reported": not_reported, "unit": unit or "", "flags": flags}
    changed = previous != new_state
    if mine is not None and changed and cell.final is not None and not (body.reason or "").strip():
        raise HTTPException(status_code=422, detail="This cell already has a final value; give a reason for the change")
    if mine is None:
        mine = models.ExtractionValue(
            project_id=access.project.id,
            study_id=study.id,
            field_id=field.id,
            arm_id=body.arm_id,
            extractor_id=access.user.id,
            source=body.source,
        )
        db.add(mine)
        cell.values.append(mine)
    mine.value, mine.not_reported, mine.unit, mine.flags = value, not_reported, unit or "", flags
    mine.span_ids, mine.quote, mine.derivation, mine.note = span_ids, quote, derivation, body.note
    mine.source, mine.ai_suggestion_id = body.source, suggestion.id if suggestion else None
    if changed:
        mine.approved_by_id, mine.approved_at = None, None
        mine.updated_at = models.utcnow()
        db.add(
            models.ExtractionValueRevision(
                project_id=access.project.id,
                study_id=study.id,
                field_id=field.id,
                arm_id=body.arm_id,
                extractor_id=access.user.id,
                kind="value",
                previous=previous,
                new=new_state,
                reason=(body.reason or "").strip(),
                changed_by_id=access.user.id,
            )
        )
    db.flush()
    refresh_final(db, cell, settings, access.user.id)
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="extraction.value_saved",
        entity_type="study",
        entity_id=study.id,
        details={
            "field": field.name,
            "arm_id": body.arm_id,
            "source": body.source,
            "flags": flags,
            "state": cell.state,
        },
    )
    db.commit()
    return cell_out(cell, access.user.id, settings, has_permission(access.membership.role, Permission.ADJUDICATE))


@router.delete("/studies/{study_id}/extraction/values/{value_id}")
def delete_value(
    study_id: int,
    value_id: int,
    reason: str = "",
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "extraction")
    study = _study(db, access, study_id)
    value = get_in_project(db, models.ExtractionValue, value_id, access.project.id, "Value")
    if value.study_id != study.id:
        raise HTTPException(status_code=404, detail="Value not found")
    if value.extractor_id != access.user.id and not has_permission(access.membership.role, Permission.ADJUDICATE):
        raise HTTPException(status_code=403, detail="Only the extractor can clear their value")
    settings = load_settings(db, access.project.id)
    field = get_in_project(db, models.ExtractionField, value.field_id, access.project.id, "Field")
    cell = _find_cell(db, study, field, value.arm_id, settings)
    if cell.final is not None and not reason.strip():
        raise HTTPException(
            status_code=422, detail="This cell already has a final value; give a reason for clearing it"
        )
    db.add(
        models.ExtractionValueRevision(
            project_id=access.project.id,
            study_id=study.id,
            field_id=field.id,
            arm_id=value.arm_id,
            extractor_id=value.extractor_id,
            kind="value",
            previous=_value_state(value),
            new=None,
            reason=reason.strip(),
            changed_by_id=access.user.id,
        )
    )
    cell.values = [v for v in cell.values if v.id != value.id]
    db.delete(value)
    db.flush()
    refresh_final(db, cell, settings, access.user.id)
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="extraction.value_cleared",
        entity_type="study",
        entity_id=study.id,
        details={"field": field.name, "arm_id": value.arm_id, "reason": reason.strip()},
    )
    db.commit()
    return Response(status_code=204)


@router.post("/extraction/values/{value_id}/approve")
def approve_imputation(
    value_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """A second reviewer approves an imputed value, which the dataset can't be locked without."""
    require_stage_open(db, access.project.id, "extraction")
    value = get_in_project(db, models.ExtractionValue, value_id, access.project.id, "Value")
    if "imputed" not in value.flags:
        raise HTTPException(status_code=409, detail="Only imputed values need approval")
    if value.extractor_id == access.user.id:
        raise HTTPException(status_code=409, detail="Someone other than the extractor approves an imputed value")
    value.approved_by_id, value.approved_at = access.user.id, models.utcnow()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="extraction.imputation_approved",
        entity_type="study",
        entity_id=value.study_id,
        details={"value_id": value.id, "field_id": value.field_id, "derivation": value.derivation},
    )
    db.commit()
    field = get_in_project(db, models.ExtractionField, value.field_id, access.project.id, "Field")
    return value_out(value, field)


class FinalIn(BaseModel):
    field_id: int
    arm_id: int | None = None
    # Take an extractor's value as final, or give the value directly.
    from_value_id: int | None = None
    value: dict[str, Any] | None = None
    not_reported: bool = False
    unit: str = Field("", max_length=40)
    span_ids: list[int] = Field(default_factory=list, max_length=50)
    rationale: str = Field(min_length=10, max_length=5000)


@router.put("/studies/{study_id}/extraction/finals")
def reconcile(
    study_id: int,
    body: FinalIn,
    access: ProjectAccess = Depends(project_access(Permission.ADJUDICATE)),
    db: Session = Depends(get_db),
):
    """Set a cell's final value after reviewing the extractors' values, with a rationale."""
    require_stage_open(db, access.project.id, "extraction")
    study = _study(db, access, study_id)
    field = _field_and_arm(db, access, study, body.field_id, body.arm_id)
    settings = load_settings(db, access.project.id)
    cell = _find_cell(db, study, field, body.arm_id, settings)
    before = final_snapshot(cell.final)
    final = cell.final
    if final is None:
        final = models.ExtractionFinal(
            project_id=access.project.id, study_id=study.id, field_id=field.id, arm_id=body.arm_id, source="reconciled"
        )
        db.add(final)
        cell.final = final
    if body.from_value_id is not None:
        source = next((v for v in cell.values if v.id == body.from_value_id), None)
        if source is None:
            raise HTTPException(status_code=404, detail="That value isn't one of this cell's values")
        final.value, final.not_reported, final.unit = source.value, source.not_reported, source.unit
        final.span_ids, final.flags, final.derivation = list(source.span_ids), list(source.flags), source.derivation
    else:
        value = None
        derivation = None
        unit = body.unit
        if not body.not_reported:
            try:
                value = validate(field, body.value or {})
            except InvalidValue as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
            value, unit, derivation = _normalize_units(field, value, unit)
        if set(body.span_ids) - _allowed_span_ids(db, study):
            raise HTTPException(status_code=422, detail="Evidence must come from this study's documents")
        final.value, final.not_reported, final.unit = value, body.not_reported, unit or ""
        final.span_ids, final.flags, final.derivation = (
            list(body.span_ids),
            ["unit_converted"] if derivation else [],
            derivation,
        )
    final.source, final.rationale = "reconciled", body.rationale.strip()
    final.decided_by_id, final.decided_at = access.user.id, models.utcnow()
    db.add(
        models.ExtractionValueRevision(
            project_id=access.project.id,
            study_id=study.id,
            field_id=field.id,
            arm_id=body.arm_id,
            kind="final",
            previous=before,
            new=final_snapshot(final),
            reason=final.rationale,
            changed_by_id=access.user.id,
        )
    )
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="extraction.reconciled",
        entity_type="study",
        entity_id=study.id,
        details={"field": field.name, "arm_id": body.arm_id, "rationale": final.rationale},
    )
    db.commit()
    cell = _find_cell(db, study, field, body.arm_id, settings)
    return cell_out(cell, access.user.id, settings, True)


@router.get("/extraction/progress")
def progress(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)):
    settings = load_settings(db, access.project.id)
    studies = included_studies(db, access.project.id)
    cells = build_cells(db, studies, project_fields(db, access.project.id), settings)
    per_study: dict[int, dict] = {
        study.id: {
            "study_id": study.id,
            "label": study.label,
            "cells": 0,
            "settled": 0,
            "discrepancies": 0,
            "awaiting": 0,
            "missing_required": 0,
        }
        for study in studies
    }
    discrepancies = []
    for cell in cells:
        row = per_study[cell.study.id]
        row["cells"] += 1
        row["settled"] += cell.state in ("final", "agreed", "reconciled")
        row["discrepancies"] += cell.state == "discrepancy"
        row["awaiting"] += cell.state in ("awaiting_second_extractor", "awaiting_ai")
        row["missing_required"] += cell.field.required and cell.state not in ("final", "agreed", "reconciled")
        if cell.state == "discrepancy":
            discrepancies.append(
                {
                    "study_id": cell.study.id,
                    "study": cell.study.label,
                    "field_id": cell.field.id,
                    "field": cell.field.name,
                    "arm_id": cell.arm.id if cell.arm else None,
                    "arm": cell.arm.label if cell.arm else None,
                }
            )
    return {
        "totals": asdict(extraction_progress(db, access.project.id)),
        "studies": list(per_study.values()),
        "discrepancies": discrepancies,
        "mode": settings.extraction.mode,
    }


@router.get("/studies/{study_id}/extraction/history")
def cell_history(
    study_id: int,
    field_id: int | None = None,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """Changes to the study's values, newest first. Other extractors' changes are shown only to reconcilers."""
    study = _study(db, access, study_id)
    can_reconcile = has_permission(access.membership.role, Permission.ADJUDICATE)
    query = (
        select(models.ExtractionValueRevision)
        .where(models.ExtractionValueRevision.study_id == study.id)
        .options(selectinload(models.ExtractionValueRevision.changed_by))
        .order_by(models.ExtractionValueRevision.id.desc())
        .limit(500)
    )
    if field_id is not None:
        query = query.where(models.ExtractionValueRevision.field_id == field_id)
    return [
        {
            "id": revision.id,
            "field_id": revision.field_id,
            "arm_id": revision.arm_id,
            "kind": revision.kind,
            "previous": revision.previous,
            "new": revision.new,
            "reason": revision.reason,
            "changed_by": revision.changed_by.full_name if revision.changed_by else None,
            "changed_at": revision.changed_at,
        }
        for revision in db.scalars(query)
        if can_reconcile or revision.kind == "final" or revision.extractor_id == access.user.id
    ]


# --- Calculations ---


class ConvertIn(BaseModel):
    method: str = Field(max_length=60)
    inputs: dict[str, float | int | bool | None]


@router.post("/extraction/convert")
def run_conversion(body: ConvertIn, access: ProjectAccess = Depends(project_access(Permission.EXTRACT))):
    """Calculate a statistic from what a report gives, with the formula and reference to save as its derivation."""
    try:
        return asdict(convert(body.method, {key: value for key, value in body.inputs.items() if value is not None}))
    except ConversionError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/studies/{study_id}/extraction/imputation")
def imputation_options(
    study_id: int,
    field_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Standard deviations from other included studies' final values of the same continuous field, to impute a
    missing SD from. Imputed values need a second reviewer's approval and a sensitivity analysis."""
    study = _study(db, access, study_id)
    field = get_in_project(db, models.ExtractionField, field_id, access.project.id, "Field")
    if field.field_type != "continuous":
        raise HTTPException(status_code=422, detail="Standard deviations can only be imputed for continuous outcomes")
    labels = {s.id: s.label for s in included_studies(db, access.project.id)}
    candidates = []
    for final in db.scalars(
        select(models.ExtractionFinal).where(
            models.ExtractionFinal.field_id == field.id, models.ExtractionFinal.study_id != study.id
        )
    ):
        value = final.value or {}
        if (
            final.study_id in labels
            and isinstance(value.get("sd"), int | float)
            and isinstance(value.get("n"), int | float)
            and value["n"] > 1
        ):
            candidates.append(
                {"study": labels[final.study_id], "study_id": final.study_id, "sd": value["sd"], "n": value["n"]}
            )
    suggestions = []
    if candidates:
        weights = sum(c["n"] - 1 for c in candidates)
        pooled = math.sqrt(sum((c["n"] - 1) * c["sd"] ** 2 for c in candidates) / weights)
        suggestions = [
            {
                "method": "pooled_sd_from_other_studies",
                "sd": pooled,
                "description": f"Pooled SD of {len(candidates)} other studies, weighted by degrees of freedom",
            },
            {
                "method": "largest_sd_from_other_studies",
                "sd": max(c["sd"] for c in candidates),
                "description": "The largest SD among other studies (a conservative choice)",
            },
        ]
    return {
        "candidates": candidates,
        "suggestions": suggestions,
        "reference": "Cochrane Handbook chapter 6 on imputing SDs; test the choice in a sensitivity analysis",
    }


# --- Author contacts ---


class ContactIn(BaseModel):
    contact_name: str = Field(min_length=1, max_length=200)
    email: str = Field(pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$", max_length=320)
    field_ids: list[int] = Field(default_factory=list, max_length=100)
    questions: str = Field(min_length=1, max_length=20_000)
    reminder_due: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")


class ContactUpdate(BaseModel):
    status: Literal["draft", "sent", "replied", "no_response", "closed"] | None = None
    reminder_due: str | None = Field(None, pattern=r"^\d{4}-\d{2}-\d{2}$")
    response_summary: str | None = Field(None, max_length=20_000)
    questions: str | None = Field(None, min_length=1, max_length=20_000)


class MessageIn(BaseModel):
    direction: Literal["outgoing", "incoming"]
    subject: str = Field("", max_length=300)
    body: str = Field(min_length=1, max_length=50_000)
    occurred_at: datetime | None = None


def _contacts_open(db: Session, project_id: int) -> None:
    for stage in ("full_text_screening", "extraction"):
        try:
            require_stage_open(db, project_id, stage)
            return
        except WorkflowError:
            continue
    raise WorkflowError("Author contacts can be changed while full-text screening or extraction is open.")


def contact_out(contact: models.AuthorContact) -> dict:
    today = date.today().isoformat()
    return {
        "id": contact.id,
        "study_id": contact.study_id,
        "contact_name": contact.contact_name,
        "email": contact.email,
        "field_ids": contact.field_ids,
        "questions": contact.questions,
        "status": contact.status,
        "reminder_due": contact.reminder_due,
        "reminder_overdue": contact.status == "sent" and (contact.reminder_due or "9999-12-31") <= today,
        "response_summary": contact.response_summary,
        "created_at": contact.created_at,
        "updated_at": contact.updated_at,
        "messages": [
            {
                "id": message.id,
                "direction": message.direction,
                "subject": message.subject,
                "body": message.body,
                "occurred_at": message.occurred_at,
            }
            for message in contact.messages
        ],
    }


@router.get("/author-contacts")
def list_contacts(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    contacts = db.scalars(
        select(models.AuthorContact)
        .where(models.AuthorContact.project_id == access.project.id)
        .options(selectinload(models.AuthorContact.messages))
        .order_by(models.AuthorContact.id)
    )
    return [contact_out(contact) for contact in contacts]


@router.post("/studies/{study_id}/author-contacts", status_code=201)
def create_contact(
    study_id: int,
    body: ContactIn,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    _contacts_open(db, access.project.id)
    study = _study(db, access, study_id)
    for field_id in body.field_ids:
        get_in_project(db, models.ExtractionField, field_id, access.project.id, "Field")
    contact = models.AuthorContact(
        project_id=access.project.id,
        study_id=study.id,
        contact_name=body.contact_name.strip(),
        email=body.email.strip(),
        field_ids=list(dict.fromkeys(body.field_ids)),
        questions=body.questions.strip(),
        status="draft",
        reminder_due=body.reminder_due,
        created_by_id=access.user.id,
    )
    db.add(contact)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="author_contact.created",
        entity_type="study",
        entity_id=study.id,
        details={"contact_id": contact.id, "field_ids": contact.field_ids},
    )
    db.commit()
    db.refresh(contact)
    return contact_out(contact)


@router.patch("/author-contacts/{contact_id}")
def update_contact(
    contact_id: int,
    body: ContactUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    _contacts_open(db, access.project.id)
    contact = get_in_project(db, models.AuthorContact, contact_id, access.project.id, "Author contact")
    changes = body.model_dump(exclude_none=True)
    for name, value in changes.items():
        setattr(contact, name, value.strip() if isinstance(value, str) else value)
    if changes:
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="author_contact.updated",
            entity_type="study",
            entity_id=contact.study_id,
            details={"contact_id": contact.id, **{k: v for k, v in changes.items() if k != "response_summary"}},
        )
    db.commit()
    return contact_out(contact)


@router.post("/author-contacts/{contact_id}/messages", status_code=201)
def log_message(
    contact_id: int,
    body: MessageIn,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Log correspondence sent from or received in the reviewer's own email. OmniReview doesn't send email itself."""
    _contacts_open(db, access.project.id)
    contact = get_in_project(db, models.AuthorContact, contact_id, access.project.id, "Author contact")
    contact.messages.append(
        models.AuthorContactMessage(
            direction=body.direction,
            subject=body.subject.strip(),
            body=body.body,
            occurred_at=body.occurred_at or datetime.now(UTC),
            logged_by_id=access.user.id,
        )
    )
    if body.direction == "outgoing" and contact.status == "draft":
        contact.status = "sent"
    elif body.direction == "incoming" and contact.status in ("draft", "sent", "no_response"):
        contact.status = "replied"
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="author_contact.message_logged",
        entity_type="study",
        entity_id=contact.study_id,
        details={"contact_id": contact.id, "direction": body.direction, "status": contact.status},
    )
    db.commit()
    db.refresh(contact)
    return contact_out(contact)


@router.get("/studies/{study_id}/author-contacts/draft")
def draft_request(
    study_id: int,
    field_ids: str = "",
    contact_name: str = "",
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """A draft data request listing the chosen fields and what's missing for each."""
    study = _study(db, access, study_id)
    settings = load_settings(db, access.project.id)
    wanted = {int(part) for part in field_ids.split(",") if part.strip().isdigit()}
    fields = [f for f in project_fields(db, access.project.id) if not wanted or f.id in wanted]
    lines = []
    for cell in build_cells(db, [study], fields, settings):
        if wanted and cell.field.id not in wanted:
            continue
        basis = cell.final or (cell.values[0] if cell.values else None)
        where = f" ({cell.arm.label})" if cell.arm else ""
        if basis is None or basis.not_reported:
            lines.append(f"- {cell.field.name}{where}")
        elif missing := missing_components(cell.field, basis.value):
            lines.append(f"- {cell.field.name}{where}: {', '.join(missing)}")
    primary = next(
        (r.record for r in study.reports if r.is_primary), study.reports[0].record if study.reports else None
    )
    title = primary.title if primary else study.label
    doi = f" (doi:{primary.doi})" if primary and primary.doi else ""
    body = "\n".join(
        [
            f"Dear {contact_name.strip() or 'author'},",
            "",
            f'We are conducting a systematic review, "{access.project.title}", which includes your study '
            f'"{title}"{doi}.',
            "We couldn't find the following information in the published report(s):",
            "",
            *(lines or ["- (choose the items to ask about)"]),
            "",
            "Would you be able to share these data, or tell us where they are reported? Summary data are enough; we "
            "don't need individual participant data. We will acknowledge your help in the review.",
            "",
            "Kind regards,",
            access.user.full_name,
        ]
    )
    return {"subject": f"Data request for a systematic review: {study.label}", "body": body, "missing_items": lines}


# --- Export ---


@router.get("/extraction/export")
def export_dataset(
    format: Literal["csv", "json", "xlsx", "bundle"] = "csv",
    source: Literal["locked", "current"] = "locked",
    access: ProjectAccess = Depends(project_access(Permission.EXPORT)),
    db: Session = Depends(get_db),
):
    """The locked dataset from extraction sign-off (or the current, unlocked data) as CSV, JSON, XLSX, or a ZIP with
    R and Python loaders."""
    if source == "locked":
        snapshot = db.scalar(
            select(models.StageSnapshot)
            .where(models.StageSnapshot.project_id == access.project.id, models.StageSnapshot.stage == "extraction")
            .order_by(models.StageSnapshot.version.desc())
            .limit(1)
        )
        if snapshot is None:
            raise HTTPException(
                status_code=404, detail="Extraction hasn't been signed off yet; export the current data instead"
            )
        dataset = snapshot.content
        metadata: dict[str, Any] = {
            "project": access.project.title,
            "dataset": f"Locked at extraction sign-off, version {snapshot.version}",
            "locked_at": snapshot.created_at.isoformat(),
            "sha256": snapshot.sha256,
            "protocol_version": snapshot.content.get("protocol_version"),
        }
    else:
        dataset = dataset_content(db, access.project.id)
        metadata = {
            "project": access.project.title,
            "dataset": "Current values (not locked)",
            "exported_at": datetime.now(UTC).isoformat(),
        }
    content, media_type, extension = {
        "csv": (lambda: to_csv(dataset), "text/csv", "csv"),
        "json": (lambda: to_json(dataset, metadata), "application/json", "json"),
        "xlsx": (
            lambda: to_xlsx(dataset, metadata),
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            "xlsx",
        ),
        "bundle": (lambda: bundle(dataset, metadata), "application/zip", "zip"),
    }[format]
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="extraction.exported",
        entity_type="project",
        entity_id=access.project.id,
        details={"format": format, "source": source},
    )
    db.commit()
    return Response(
        content(),
        media_type=media_type,
        headers={
            "Content-Disposition": f'attachment; filename="omnireview-dataset-{access.project.id}-{source}.{extension}"'
        },
    )
