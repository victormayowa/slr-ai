"""Protocol export (Word, Markdown, PROSPERO fields) and registration records: PROSPERO, OSF, other registries, or a
waiver with a reason.

OmniReview never submits a registration for the team. PROSPERO has no submission API, and OSF registrations are
public and permanent, so reviewers complete those steps themselves and record the result here.
"""

import asyncio
import re
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from audit import record_event
from database import get_db
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from protocol_export import (
    latest_protocol_version,
    prospero_fields,
    protocol_document,
    protocol_is_locked,
    to_docx,
    to_markdown,
)
from rate_limiting import ai_rate_limit
from services.osf import OSFError, deposit_protocol

router = APIRouter(prefix="/api/projects/{project_id}", tags=["registration"])

DOCX_TYPE = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"


class RegistrationCreate(BaseModel):
    registry: Literal["PROSPERO", "OSF", "other"]
    registry_name: str | None = Field(None, max_length=40)
    status: Literal["submitted", "registered"]
    registration_id: str = Field("", max_length=100)
    url: str | None = Field(None, max_length=500)


class RegistrationUpdate(BaseModel):
    status: Literal["submitted", "registered"] | None = None
    registration_id: str | None = Field(None, max_length=100)
    url: str | None = Field(None, max_length=500)
    # Confirms the registry record was updated with the latest protocol amendment.
    covers_latest_version: bool = False


class WaiverRequest(BaseModel):
    reason: str = Field(min_length=20, max_length=2000)


class OSFDepositRequest(BaseModel):
    # Used for this request only; never stored or logged.
    token: str = Field(min_length=10, max_length=500)


def registration_out(registration: models.ProtocolRegistration, latest_version: int) -> dict:
    return {
        "id": registration.id,
        "registry": registration.registry_name,
        "status": registration.status,
        "registration_id": registration.registration_id,
        "url": registration.url,
        "protocol_version": registration.protocol_version,
        "latest_protocol_version": latest_version,
        "waiver_reason": registration.waiver_reason,
        "created_by": registration.created_by.full_name if registration.created_by else None,
        "created_at": registration.created_at,
        "updated_at": registration.updated_at,
    }


def _require_locked(db: Session, project: models.Project) -> int:
    version = latest_protocol_version(db, project.id)
    if not protocol_is_locked(db, project.id) or version == 0:
        raise HTTPException(status_code=409, detail="Sign off the protocol before registering it")
    return version


def _file_stem(project: models.Project) -> str:
    return re.sub(r"[^a-z0-9]+", "-", project.title.lower()).strip("-")[:60] or "protocol"


def _audit(db: Session, access: ProjectAccess, action: str, entity_id: int, details: dict) -> None:
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=action,
        entity_type="protocol_registration",
        entity_id=entity_id,
        details=details,
    )


@router.get("/protocol-export")
def export_protocol(
    format: Literal["markdown", "docx"] = Query("docx"),
    access: ProjectAccess = Depends(project_access(Permission.EXPORT)),
    db: Session = Depends(get_db),
):
    title, blocks = protocol_document(db, access.project)
    version = latest_protocol_version(db, access.project.id) if protocol_is_locked(db, access.project.id) else None
    suffix = f"-v{version}" if version else "-draft"
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="protocol.exported",
        entity_type="project",
        entity_id=access.project.id,
        details={"format": format, "protocol_version": version},
    )
    db.commit()
    if format == "markdown":
        content, media_type, extension = to_markdown(title, blocks).encode(), "text/markdown; charset=utf-8", "md"
    else:
        content, media_type, extension = to_docx(title, blocks), DOCX_TYPE, "docx"
    file_name = f"{_file_stem(access.project)}-protocol{suffix}.{extension}"
    return Response(
        content, media_type=media_type, headers={"Content-Disposition": f'attachment; filename="{file_name}"'}
    )


@router.get("/prospero-fields")
def get_prospero_fields(
    access: ProjectAccess = Depends(project_access(Permission.EXPORT)), db: Session = Depends(get_db)
):
    return prospero_fields(db, access.project)


@router.get("/registrations")
def list_registrations(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    latest = latest_protocol_version(db, access.project.id)
    registrations = db.scalars(
        select(models.ProtocolRegistration)
        .where(models.ProtocolRegistration.project_id == access.project.id)
        .order_by(models.ProtocolRegistration.id)
    )
    return [registration_out(registration, latest) for registration in registrations]


@router.post("/registrations", status_code=201)
def record_registration(
    body: RegistrationCreate,
    access: ProjectAccess = Depends(project_access(Permission.APPROVE_PROTOCOL)),
    db: Session = Depends(get_db),
):
    """Record a registration the team made on PROSPERO, OSF, or another registry."""
    version = _require_locked(db, access.project)
    registry: str = body.registry
    if registry == "other":
        if not (body.registry_name or "").strip():
            raise HTTPException(status_code=422, detail="Name the registry")
        registry = (body.registry_name or "").strip()
    registration = models.ProtocolRegistration(
        project_id=access.project.id,
        registry_name=registry,
        status=body.status,
        registration_id=body.registration_id.strip(),
        url=(body.url or "").strip() or None,
        protocol_version=version,
        created_by_id=access.user.id,
    )
    db.add(registration)
    db.flush()
    _audit(
        db,
        access,
        "registration.recorded",
        registration.id,
        {
            "registry": registry,
            "status": body.status,
            "registration_id": registration.registration_id,
            "protocol_version": version,
        },
    )
    db.commit()
    return registration_out(registration, version)


@router.patch("/registrations/{registration_id}")
def update_registration(
    registration_id: int,
    body: RegistrationUpdate,
    access: ProjectAccess = Depends(project_access(Permission.APPROVE_PROTOCOL)),
    db: Session = Depends(get_db),
):
    registration = get_in_project(db, models.ProtocolRegistration, registration_id, access.project.id, "Registration")
    if registration.status == "waived":
        raise HTTPException(status_code=409, detail="A waiver can't be updated; record a registration instead")
    latest = latest_protocol_version(db, access.project.id)
    changes: dict = {}
    for name in ("status", "registration_id", "url"):
        value = getattr(body, name)
        if value is not None and value != getattr(registration, name):
            changes[name] = value
            setattr(registration, name, value)
    if body.covers_latest_version and registration.protocol_version != latest:
        changes["protocol_version"] = latest
        registration.protocol_version = latest
    if changes:
        registration.updated_at = models.utcnow()
        _audit(db, access, "registration.updated", registration.id, {"changes": changes})
    db.commit()
    return registration_out(registration, latest)


@router.post("/registrations/waiver", status_code=201)
def waive_registration(
    body: WaiverRequest,
    access: ProjectAccess = Depends(project_access(Permission.APPROVE_PROTOCOL)),
    db: Session = Depends(get_db),
):
    """Record that the protocol won't be registered, and why (for example, a student exercise)."""
    version = _require_locked(db, access.project)
    registration = models.ProtocolRegistration(
        project_id=access.project.id,
        registry_name="none",
        status="waived",
        protocol_version=version,
        waiver_reason=body.reason.strip(),
        created_by_id=access.user.id,
    )
    db.add(registration)
    db.flush()
    _audit(db, access, "registration.waived", registration.id, {"reason": registration.waiver_reason})
    db.commit()
    return registration_out(registration, version)


@router.post("/registrations/osf", status_code=201, dependencies=[Depends(ai_rate_limit)])
async def deposit_on_osf(
    body: OSFDepositRequest,
    access: ProjectAccess = Depends(project_access(Permission.APPROVE_PROTOCOL)),
    db: Session = Depends(get_db),
):
    """Create a private OSF project holding the locked protocol, ready for the team to register on OSF."""
    version = _require_locked(db, access.project)
    title, blocks = protocol_document(db, access.project)
    file_name = f"{_file_stem(access.project)}-protocol-v{version}.docx"
    description = f"Systematic review protocol, version {version}, exported from OmniReview."
    try:
        deposit = await asyncio.to_thread(
            deposit_protocol, body.token, title, description, file_name, to_docx(title, blocks), DOCX_TYPE
        )
    except OSFError as exc:
        raise HTTPException(status_code=exc.status_code, detail=str(exc)) from exc

    registration = models.ProtocolRegistration(
        project_id=access.project.id,
        registry_name="OSF",
        status="deposited",
        registration_id=deposit.node_id,
        url=deposit.url,
        protocol_version=version,
        created_by_id=access.user.id,
    )
    db.add(registration)
    db.flush()
    _audit(
        db,
        access,
        "registration.deposited",
        registration.id,
        {
            "registry": "OSF",
            "node_id": deposit.node_id,
            "url": deposit.url,
            "file_name": file_name,
            "protocol_version": version,
        },
    )
    db.commit()
    return registration_out(registration, version)
