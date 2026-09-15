"""Studies and their reports: linking reports of the same study, splitting them, and defining each study's arms."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from audit import record_event
from database import get_db
from extraction_data import included_records, included_studies, latest_suggestions
from permissions import Permission, has_permission
from projects_routes import ProjectAccess, get_in_project, project_access
from records_routes import record_brief
from review_settings import review_policy
from studies import ensure_studies, link_candidates, study_has_extraction, study_label
from workflow import WorkflowError, require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["studies"])


class MergeRequest(BaseModel):
    other_study_id: int


class SplitRequest(BaseModel):
    record_id: int


class LinkDecision(BaseModel):
    record_id: int
    other_record_id: int


class StudyUpdate(BaseModel):
    label: str | None = Field(None, min_length=1, max_length=300)
    notes: str | None = Field(None, max_length=10_000)
    registry_ids: list[str] | None = Field(None, max_length=20)


class ArmIn(BaseModel):
    id: int | None = None
    label: str = Field(min_length=1, max_length=200)
    description: str = Field("", max_length=2000)


class ArmsUpdate(BaseModel):
    arms: list[ArmIn] = Field(max_length=30)


def require_studies_open(db: Session, project_id: int) -> None:
    for stage in ("full_text_screening", "extraction"):
        try:
            require_stage_open(db, project_id, stage)
            return
        except WorkflowError:
            continue
    raise WorkflowError("Studies can be changed while full-text screening or extraction is open.")


def study_out(study: models.Study) -> dict:
    return {
        "id": study.id,
        "label": study.label,
        "registry_ids": study.registry_ids,
        "notes": study.notes,
        "reports": [
            {**record_brief(report.record), "is_primary": report.is_primary, "record_id": report.record_id}
            for report in study.reports
        ],
        "arms": [{"id": arm.id, "label": arm.label, "description": arm.description} for arm in study.arms],
    }


def _study(db: Session, access: ProjectAccess, study_id: int) -> models.Study:
    return get_in_project(db, models.Study, study_id, access.project.id, "Study")


def _audit(db: Session, access: ProjectAccess, action: str, study_id: int, details: dict) -> None:
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=action,
        entity_type="study",
        entity_id=study_id,
        details=details,
    )


@router.get("/studies")
def list_studies(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """Studies with at least one report included in the review. Reports included since are given studies of their own
    when someone who can extract views the list."""
    policy = review_policy(db, access.project.id)
    if has_permission(access.membership.role, Permission.EXTRACT):
        try:
            require_studies_open(db, access.project.id)
            ensure_studies(db, access.project.id, included_records(db, access.project.id, policy), access.user.id)
            db.commit()
        except WorkflowError:
            pass
    return [study_out(study) for study in included_studies(db, access.project.id, policy)]


@router.get("/studies/link-candidates")
def study_link_candidates(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """Reports in different studies that may describe the same study, with the reasons."""
    studies = included_studies(db, access.project.id)
    study_of = {report.record_id: study for study in studies for report in study.reports}
    return [
        {
            "record": record_brief(_report(study_of, c.record_id)),
            "other": record_brief(_report(study_of, c.other_record_id)),
            "study_id": study_of[c.record_id].id,
            "other_study_id": study_of[c.other_record_id].id,
            "score": c.score,
            "reasons": c.reasons,
        }
        for c in link_candidates(db, access.project.id, studies)
    ]


def _report(study_of: dict[int, models.Study], record_id: int) -> models.Record:
    return next(report.record for report in study_of[record_id].reports if report.record_id == record_id)


@router.post("/studies/{study_id}/merge")
def merge_studies(
    study_id: int,
    body: MergeRequest,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Link the other study's reports (and arms) into this study, because they describe the same study."""
    require_studies_open(db, access.project.id)
    study, other = _study(db, access, study_id), _study(db, access, body.other_study_id)
    if study.id == other.id:
        raise HTTPException(status_code=400, detail="Choose a different study to merge")
    if study_has_extraction(db, other.id):
        raise HTTPException(
            status_code=409,
            detail=f"{other.label} already has extracted data. Clear its values before merging it into another study.",
        )
    moved = [report.record_id for report in other.reports]
    for report in list(other.reports):
        # Move through the collections, so deleting the emptied study doesn't delete its former reports.
        other.reports.remove(report)
        report.is_primary = False
        study.reports.append(report)
    labels = {arm.label.casefold() for arm in study.arms}
    for arm in list(other.arms):
        if arm.label.casefold() not in labels:
            study.arms.append(models.StudyArm(label=arm.label, description=arm.description, position=len(study.arms)))
    study.registry_ids = list(dict.fromkeys([*study.registry_ids, *other.registry_ids]))
    db.flush()
    db.delete(other)
    _audit(db, access, "study.merged", study.id, {"merged_study_id": body.other_study_id, "record_ids": moved})
    db.commit()
    db.refresh(study)
    return study_out(study)


@router.post("/studies/{study_id}/split")
def split_report(
    study_id: int,
    body: SplitRequest,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Move a report out of the study into a study of its own."""
    require_studies_open(db, access.project.id)
    study = _study(db, access, study_id)
    report = next((r for r in study.reports if r.record_id == body.record_id), None)
    if report is None:
        raise HTTPException(status_code=404, detail="That report isn't part of this study")
    if len(study.reports) == 1:
        raise HTTPException(status_code=400, detail="A study's only report can't be split off")
    new_study = models.Study(
        project_id=access.project.id, label=study_label(report.record), created_by_id=access.user.id
    )
    db.add(new_study)
    db.flush()
    was_primary = report.is_primary
    study.reports.remove(report)
    report.is_primary = True
    new_study.reports.append(report)
    if was_primary:
        remaining = next(r for r in study.reports if r.record_id != body.record_id)
        remaining.is_primary = True
    db.flush()
    _audit(db, access, "study.report_split", study.id, {"record_id": body.record_id, "new_study_id": new_study.id})
    db.commit()
    return [study_out(item) for item in included_studies(db, access.project.id)]


@router.post("/study-link-decisions", status_code=201)
def reject_link(
    body: LinkDecision,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Record that two reports describe different studies, so they aren't suggested for linking again."""
    require_studies_open(db, access.project.id)
    first, second = sorted((body.record_id, body.other_record_id))
    for record_id in (first, second):
        get_in_project(db, models.Record, record_id, access.project.id, "Record")
    exists = db.scalar(
        select(models.StudyLinkDecision.id).where(
            models.StudyLinkDecision.record_id == first, models.StudyLinkDecision.other_record_id == second
        )
    )
    if exists is None:
        db.add(
            models.StudyLinkDecision(
                project_id=access.project.id,
                record_id=first,
                other_record_id=second,
                decision="different_studies",
                decided_by_id=access.user.id,
            )
        )
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="study.link_rejected",
            entity_type="record",
            entity_id=first,
            details={"other_record_id": second},
        )
    db.commit()
    return {"record_id": first, "other_record_id": second, "decision": "different_studies"}


@router.patch("/studies/{study_id}")
def update_study(
    study_id: int,
    body: StudyUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    require_studies_open(db, access.project.id)
    study = _study(db, access, study_id)
    changes = body.model_dump(exclude_none=True)
    if "label" in changes:
        changes["label"] = changes["label"].strip()
    if "registry_ids" in changes:
        changes["registry_ids"] = list(dict.fromkeys(i.strip() for i in changes["registry_ids"] if i.strip()))
    for name, value in changes.items():
        setattr(study, name, value)
    if changes:
        _audit(db, access, "study.updated", study.id, changes)
    db.commit()
    return study_out(study)


@router.put("/studies/{study_id}/arms")
def replace_arms(
    study_id: int,
    body: ArmsUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Set the study's arms. Arms that already have extracted values can be renamed but not removed."""
    require_studies_open(db, access.project.id)
    study = _study(db, access, study_id)
    labels = [arm.label.strip().casefold() for arm in body.arms]
    if len(set(labels)) != len(labels):
        raise HTTPException(status_code=422, detail="Each arm needs a different name")
    existing = {arm.id: arm for arm in study.arms}
    kept_ids = {arm.id for arm in body.arms if arm.id is not None}
    if kept_ids - existing.keys():
        raise HTTPException(status_code=404, detail="Some arms weren't found in this study")
    removed = [arm for arm_id, arm in existing.items() if arm_id not in kept_ids]
    for arm in removed:
        in_use = db.scalar(select(models.ExtractionValue.id).where(models.ExtractionValue.arm_id == arm.id).limit(1))
        if in_use is not None:
            raise HTTPException(
                status_code=409, detail=f"The arm {arm.label} has extracted values and can't be removed"
            )
    for arm in removed:
        study.arms.remove(arm)
    db.flush()
    for position, item in enumerate(body.arms):
        current: models.StudyArm | None = existing.get(item.id) if item.id is not None else None
        if current is None:
            current = models.StudyArm(label=item.label.strip(), description=item.description, position=position)
            study.arms.append(current)
        else:
            current.label, current.description, current.position = item.label.strip(), item.description, position
    db.flush()
    _audit(db, access, "study.arms_updated", study.id, {"arms": [arm.label for arm in body.arms]})
    db.commit()
    db.refresh(study)
    return study_out(study)


@router.post("/studies/{study_id}/arms/from-ai")
def arms_from_ai(
    study_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Add the arms named in the study's latest AI extraction that the study doesn't have yet."""
    require_studies_open(db, access.project.id)
    study = _study(db, access, study_id)
    suggested = [
        s.arm_label.strip() for s in latest_suggestions(db, [study.id]).get(study.id, []) if s.arm_label.strip()
    ]
    labels = {arm.label.casefold() for arm in study.arms}
    added = []
    for label in dict.fromkeys(suggested):
        if label.casefold() not in labels:
            study.arms.append(models.StudyArm(label=label[:200], position=len(study.arms)))
            labels.add(label.casefold())
            added.append(label)
    if added:
        _audit(db, access, "study.arms_added_from_ai", study.id, {"arms": added})
    db.commit()
    db.refresh(study)
    return study_out(study)
