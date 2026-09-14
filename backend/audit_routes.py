from fastapi import APIRouter, Depends, Query
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

import models
from audit import verify_chain
from database import get_db
from permissions import Permission
from projects_routes import ProjectAccess, project_access

router = APIRouter(prefix="/api/projects/{project_id}", tags=["audit"])


@router.get("/audit")
def list_audit_events(
    limit: int = Query(200, ge=1, le=1000),
    access: ProjectAccess = Depends(project_access(Permission.VIEW_AUDIT)),
    db: Session = Depends(get_db),
):
    events = db.scalars(
        select(models.AuditEvent)
        .where(models.AuditEvent.project_id == access.project.id)
        .options(selectinload(models.AuditEvent.actor))
        .order_by(models.AuditEvent.id.desc())
        .limit(limit)
    ).all()
    return {
        "chain_valid": verify_chain(db, access.project.id),
        "events": [
            {
                "id": event.id,
                "action": event.action,
                "entity_type": event.entity_type,
                "entity_id": event.entity_id,
                "actor": event.actor.full_name if event.actor else None,
                "details": event.details,
                "created_at": event.created_at,
                "hash": event.hash,
            }
            for event in events
        ],
    }
