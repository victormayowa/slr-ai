"""Stage sign-off, reopening, and snapshot history for a project's review workflow."""

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import models
from database import get_db
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from workflow import STAGE_PERMISSIONS, STAGES, complete_stage, reopen_stage, snapshot_is_intact, workflow_overview

router = APIRouter(prefix="/api/projects/{project_id}/workflow", tags=["workflow"])


class CompleteStageRequest(BaseModel):
    note: str = Field(min_length=3, max_length=5000)


class ReopenStageRequest(BaseModel):
    rationale: str = Field(min_length=10, max_length=5000)


def _require_stage_permission(access: ProjectAccess, stage: str) -> None:
    if stage not in STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown workflow stage: {stage}")
    if not access.can(STAGE_PERMISSIONS[stage]):
        raise HTTPException(status_code=403, detail="Your role on this project does not allow signing off this stage")


def snapshot_out(snapshot: models.StageSnapshot, include_content: bool = False) -> dict:
    out = {
        "id": snapshot.id,
        "stage": snapshot.stage,
        "version": snapshot.version,
        "sha256": snapshot.sha256,
        "intact": snapshot_is_intact(snapshot),
        "note": snapshot.note,
        "created_by": snapshot.created_by.full_name if snapshot.created_by else None,
        "created_at": snapshot.created_at,
    }
    if include_content:
        out["content"] = snapshot.content
    return out


@router.get("")
def get_workflow(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    return workflow_overview(db, access.project, access.can)


@router.post("/{stage}/complete")
def complete_workflow_stage(
    stage: str,
    body: CompleteStageRequest,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    _require_stage_permission(access, stage)
    complete_stage(db, access.project, stage, access.user, body.note.strip())
    db.commit()
    return workflow_overview(db, access.project, access.can)


@router.post("/{stage}/reopen")
def reopen_workflow_stage(
    stage: str,
    body: ReopenStageRequest,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    _require_stage_permission(access, stage)
    reopen_stage(db, access.project, stage, access.user, body.rationale.strip())
    db.commit()
    return workflow_overview(db, access.project, access.can)


@router.get("/{stage}/snapshots")
def list_stage_snapshots(
    stage: str,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    if stage not in STAGES:
        raise HTTPException(status_code=404, detail=f"Unknown workflow stage: {stage}")
    snapshots = db.scalars(
        select(models.StageSnapshot)
        .where(models.StageSnapshot.project_id == access.project.id, models.StageSnapshot.stage == stage)
        .options(selectinload(models.StageSnapshot.created_by))
        .order_by(models.StageSnapshot.version.desc())
    )
    return [snapshot_out(snapshot) for snapshot in snapshots]


@router.get("/snapshots/{snapshot_id}")
def get_stage_snapshot(
    snapshot_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    snapshot = get_in_project(db, models.StageSnapshot, snapshot_id, access.project.id, "Snapshot")
    return snapshot_out(snapshot, include_content=True)
