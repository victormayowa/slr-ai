"""Projects and project membership. Every project route checks the caller's role against permissions.py."""

from collections.abc import Callable
from dataclasses import dataclass

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_catalog import ai_model_out, model_ref
from audit import record_event
from auth_routes import get_current_user
from database import Base, get_db
from permissions import Permission, ProjectRole, has_permission
from project_defaults import apply_project_defaults
from storage import document_storage

router = APIRouter(prefix="/api/projects", tags=["projects"])


class ProjectCreate(BaseModel):
    title: str = Field(min_length=1, max_length=300)
    description: str | None = Field(None, max_length=5000)
    organization_id: int | None = None


class ProjectUpdate(BaseModel):
    title: str | None = Field(None, min_length=1, max_length=300)
    description: str | None = Field(None, max_length=5000)


class AIModelSelection(BaseModel):
    ai_model_id: int


class MemberAdd(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: ProjectRole


class MemberUpdate(BaseModel):
    role: ProjectRole


@dataclass
class ProjectAccess:
    user: models.User
    project: models.Project
    membership: models.ProjectMember

    def can(self, permission: Permission) -> bool:
        return has_permission(self.membership.role, permission)


def project_access(permission: Permission) -> Callable[..., ProjectAccess]:
    """Dependency returning the caller's membership, provided their role grants `permission`.

    Non-members get 404 rather than 403 so project IDs don't reveal which projects exist.
    """

    def dependency(
        project_id: int,
        user: models.User = Depends(get_current_user),
        db: Session = Depends(get_db),
    ) -> ProjectAccess:
        membership = db.scalar(
            select(models.ProjectMember).where(
                models.ProjectMember.project_id == project_id, models.ProjectMember.user_id == user.id
            )
        )
        if membership is None:
            raise HTTPException(status_code=404, detail="Project not found")
        if not has_permission(membership.role, permission):
            raise HTTPException(status_code=403, detail="Your role on this project does not allow this action")
        return ProjectAccess(user=user, project=membership.project, membership=membership)

    return dependency


def get_in_project[ModelT: Base](db: Session, model: type[ModelT], row_id: int, project_id: int, label: str) -> ModelT:
    """Fetch a project-scoped row by id, returning 404 if it doesn't exist or belongs to another project."""
    row = db.get(model, row_id)
    if row is None or getattr(row, "project_id", None) != project_id:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return row


def _project_out(project: models.Project, role: str) -> dict:
    return {
        "id": project.id,
        "title": project.title,
        "description": project.description,
        "organization": (
            {"id": project.organization.id, "name": project.organization.name} if project.organization else None
        ),
        "role": role,
        "ai_model": ai_model_out(project.ai_model) if project.ai_model else None,
        "embedding_model": ai_model_out(project.embedding_model) if project.embedding_model else None,
        "member_count": len(project.members),
        "created_at": project.created_at,
    }


def _member_out(member: models.ProjectMember) -> dict:
    return {"user_id": member.user_id, "name": member.user.full_name, "email": member.user.email, "role": member.role}


def _find_member(project: models.Project, user_id: int) -> models.ProjectMember:
    member = next((m for m in project.members if m.user_id == user_id), None)
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    return member


def _require_owner(access: ProjectAccess) -> None:
    if access.membership.role != ProjectRole.OWNER:
        raise HTTPException(status_code=403, detail="Only a project owner can grant or remove the owner role")


def _ensure_not_last_owner(project: models.Project, member: models.ProjectMember) -> None:
    owners = [m for m in project.members if m.role == ProjectRole.OWNER]
    if owners == [member]:
        raise HTTPException(status_code=409, detail="A project must keep at least one owner")


@router.get("")
def list_projects(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    memberships = db.scalars(
        select(models.ProjectMember)
        .join(models.Project)
        .where(models.ProjectMember.user_id == user.id)
        .order_by(models.Project.created_at.desc(), models.Project.id.desc())
    ).all()
    return [_project_out(m.project, m.role) for m in memberships]


@router.post("", status_code=status.HTTP_201_CREATED)
def create_project(body: ProjectCreate, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    if body.organization_id is not None:
        in_organization = db.scalar(
            select(models.OrganizationMember).where(
                models.OrganizationMember.organization_id == body.organization_id,
                models.OrganizationMember.user_id == user.id,
            )
        )
        if in_organization is None:
            raise HTTPException(status_code=404, detail="Organization not found")

    project = models.Project(
        title=body.title, description=body.description, organization_id=body.organization_id, owner_id=user.id
    )
    project.members.append(models.ProjectMember(user_id=user.id, role=ProjectRole.OWNER))
    apply_project_defaults(db, project)
    db.add(project)
    db.flush()
    record_event(
        db,
        project_id=project.id,
        actor_id=user.id,
        action="project.created",
        entity_type="project",
        entity_id=project.id,
        details={"title": project.title},
    )
    db.commit()
    db.refresh(project)
    return _project_out(project, ProjectRole.OWNER)


@router.get("/{project_id}")
def get_project(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    return _project_out(access.project, access.membership.role)


@router.patch("/{project_id}")
def update_project(
    body: ProjectUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROJECT)),
    db: Session = Depends(get_db),
):
    updates = body.model_dump(exclude_unset=True)
    if "title" in updates and updates["title"] is None:
        raise HTTPException(status_code=422, detail="A project needs a title")
    for field, value in updates.items():
        setattr(access.project, field, value)
    if updates:
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="project.updated",
            entity_type="project",
            entity_id=access.project.id,
            details={"changes": updates},
        )
    db.commit()
    return _project_out(access.project, access.membership.role)


@router.put("/{project_id}/ai-model")
def set_project_ai_model(
    body: AIModelSelection,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROJECT)),
    db: Session = Depends(get_db),
):
    """Pin the model used for every AI task in the project. Earlier runs keep a record of the model they used."""
    model = db.get(models.AIModel, body.ai_model_id)
    if model is None or not model.enabled or model.purpose != "chat":
        raise HTTPException(status_code=404, detail="That AI model isn't available")
    previous = access.project.ai_model
    if previous is None or previous.id != model.id:
        access.project.ai_model = model
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="project.ai_model_changed",
            entity_type="project",
            entity_id=access.project.id,
            details={"from": model_ref(previous), "to": model_ref(model)},
        )
    db.commit()
    return _project_out(access.project, access.membership.role)


@router.put("/{project_id}/embedding-model")
def set_project_embedding_model(
    body: AIModelSelection,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROJECT)),
    db: Session = Depends(get_db),
):
    """Pin the model that embeds records. Embeddings made by other models are kept but not compared with new ones."""
    model = db.get(models.AIModel, body.ai_model_id)
    if model is None or not model.enabled or model.purpose != "embedding":
        raise HTTPException(status_code=404, detail="That embedding model isn't available")
    previous = access.project.embedding_model
    if previous is None or previous.id != model.id:
        access.project.embedding_model = model
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="project.embedding_model_changed",
            entity_type="project",
            entity_id=access.project.id,
            details={"from": model_ref(previous), "to": model_ref(model)},
        )
    db.commit()
    return _project_out(access.project, access.membership.role)


@router.delete("/{project_id}", status_code=status.HTTP_204_NO_CONTENT)
def delete_project(
    access: ProjectAccess = Depends(project_access(Permission.DELETE_PROJECT)), db: Session = Depends(get_db)
):
    project_id = access.project.id
    db.delete(access.project)
    db.commit()
    document_storage().delete_project(project_id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.get("/{project_id}/members")
def list_members(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    return [_member_out(m) for m in sorted(access.project.members, key=lambda m: m.id)]


@router.post("/{project_id}/members", status_code=status.HTTP_201_CREATED)
def add_member(
    body: MemberAdd,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_MEMBERS)),
    db: Session = Depends(get_db),
):
    if body.role == ProjectRole.OWNER:
        _require_owner(access)
    user = db.scalar(select(models.User).where(models.User.email == body.email))
    if user is None:
        raise HTTPException(status_code=404, detail="No account uses that email address. Ask them to register first.")
    if any(m.user_id == user.id for m in access.project.members):
        raise HTTPException(status_code=409, detail="That person is already a member of this project")

    member = models.ProjectMember(project_id=access.project.id, user_id=user.id, role=body.role)
    db.add(member)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="member.added",
        entity_type="user",
        entity_id=user.id,
        details={"email": user.email, "role": body.role},
    )
    db.commit()
    db.refresh(member)
    return _member_out(member)


@router.patch("/{project_id}/members/{user_id}")
def change_member_role(
    user_id: int,
    body: MemberUpdate,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_MEMBERS)),
    db: Session = Depends(get_db),
):
    member = _find_member(access.project, user_id)
    if ProjectRole.OWNER in (member.role, body.role):
        _require_owner(access)
    if member.role == ProjectRole.OWNER and body.role != ProjectRole.OWNER:
        _ensure_not_last_owner(access.project, member)
    previous_role = member.role
    member.role = body.role
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="member.role_changed",
        entity_type="user",
        entity_id=user_id,
        details={"from": previous_role, "to": body.role},
    )
    db.commit()
    return _member_out(member)


@router.delete("/{project_id}/members/{user_id}", status_code=status.HTTP_204_NO_CONTENT)
def remove_member(
    user_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    member = _find_member(access.project, user_id)
    leaving_themselves = member.user_id == access.user.id
    if not leaving_themselves and not access.can(Permission.MANAGE_MEMBERS):
        raise HTTPException(status_code=403, detail="Your role on this project does not allow this action")
    if member.role == ProjectRole.OWNER:
        if not leaving_themselves:
            _require_owner(access)
        _ensure_not_last_owner(access.project, member)
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="member.removed",
        entity_type="user",
        entity_id=user_id,
        details={"role": member.role, "left_themselves": leaving_themselves},
    )
    db.delete(member)
    db.commit()
    return Response(status_code=status.HTTP_204_NO_CONTENT)
