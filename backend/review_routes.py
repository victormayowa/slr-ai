"""Protocol setup, eligibility criteria, search strategies, and extraction fields."""

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

import models
from audit import record_event
from database import get_db
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from services.ai_protocol import PROMPT_VERSION as PROTOCOL_PROMPT_VERSION
from services.ai_protocol import generate_protocol_elements
from services.ai_screening import ROB_TOOL_DOMAINS
from services.errors import LLMError
from services.llm import Provider, model_for
from workflow import require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["protocol"])

MAX_EXTRACTION_FIELDS = 100


class ProtocolUpdate(BaseModel):
    review_type: str = Field(min_length=1, max_length=50)
    framework: str = Field(min_length=1, max_length=20)
    description: str = Field(max_length=20_000)
    suggested_criteria: str = Field(max_length=20_000)
    extraction_outline: str = Field(max_length=20_000)
    rob_tool: str = Field(max_length=30)


class GenerateProtocolRequest(BaseModel):
    provider: Provider = "gemini"


class CriterionUpdate(BaseModel):
    status: Literal["pending", "accepted", "rejected"] | None = None
    text: str | None = Field(None, min_length=1, max_length=2000)


class AcceptAllRequest(BaseModel):
    kind: Literal["inclusion", "exclusion"]


class StrategyUpdate(BaseModel):
    query: str = Field(min_length=1, max_length=10_000)


class ExtractionFieldsUpdate(BaseModel):
    names: list[str] = Field(max_length=MAX_EXTRACTION_FIELDS)


def protocol_out(protocol: models.Protocol) -> dict:
    return {
        "review_type": protocol.review_type,
        "framework": protocol.framework,
        "description": protocol.description,
        "suggested_criteria": protocol.suggested_criteria,
        "extraction_outline": protocol.extraction_outline,
        "rob_tool": protocol.rob_tool,
        "updated_at": protocol.updated_at,
    }


def criterion_out(criterion: models.Criterion) -> dict:
    return {"id": criterion.id, "kind": criterion.kind, "text": criterion.text, "status": criterion.status}


def strategy_out(strategy: models.SearchStrategy) -> dict:
    return {"id": strategy.id, "database": strategy.database, "query": strategy.query}


def _project_criteria(db: Session, project_id: int) -> list[models.Criterion]:
    return list(
        db.scalars(
            select(models.Criterion).where(models.Criterion.project_id == project_id).order_by(models.Criterion.id)
        )
    )


def _project_strategies(db: Session, project_id: int) -> list[models.SearchStrategy]:
    return list(
        db.scalars(
            select(models.SearchStrategy)
            .where(models.SearchStrategy.project_id == project_id)
            .order_by(models.SearchStrategy.id)
        )
    )


def _clean_field_names(names: list[str]) -> list[str]:
    cleaned: list[str] = []
    for name in names:
        name = name.strip()[:200]
        if name and name not in cleaned:
            cleaned.append(name)
    return cleaned


def _add_extraction_fields(project: models.Project, names: list[str]) -> list[str]:
    existing = {field.name for field in project.extraction_fields}
    added = [name for name in _clean_field_names(names) if name not in existing]
    for name in added:
        project.extraction_fields.append(models.ExtractionField(name=name, position=len(project.extraction_fields)))
    return added


@router.get("/protocol")
def get_protocol(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    if access.project.protocol is None:
        raise HTTPException(status_code=404, detail="This project has no protocol")
    return protocol_out(access.project.protocol)


@router.put("/protocol")
def update_protocol(
    body: ProtocolUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "protocol")
    if body.rob_tool not in ROB_TOOL_DOMAINS:
        raise HTTPException(status_code=422, detail=f"Unsupported risk of bias tool: {body.rob_tool}")
    protocol = access.project.protocol
    if protocol is None:
        raise HTTPException(status_code=404, detail="This project has no protocol")

    changes = {name: value for name, value in body.model_dump().items() if getattr(protocol, name) != value}
    for name, value in changes.items():
        setattr(protocol, name, value)
    if changes:
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="protocol.updated",
            entity_type="protocol",
            entity_id=protocol.id,
            details={"changes": changes},
        )
    db.commit()
    return protocol_out(protocol)


@router.post("/protocol/generate", dependencies=[Depends(ai_rate_limit)])
async def generate_protocol(
    body: GenerateProtocolRequest,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "protocol")
    project, protocol = access.project, access.project.protocol
    if protocol is None or not protocol.description.strip():
        raise HTTPException(status_code=400, detail="Describe the study in Project Setup before generating a protocol")

    research_question = "\n".join(
        [
            f"Title: {project.title}",
            f"Review Type: {protocol.review_type}",
            f"Framework: {protocol.framework}",
            f"Description: {protocol.description}",
            f"Suggested Criteria: {protocol.suggested_criteria}",
        ]
    )
    run = models.AIRun(
        project_id=project.id,
        task="protocol",
        provider=body.provider,
        model=model_for(body.provider),
        prompt_version=PROTOCOL_PROMPT_VERSION,
        triggered_by_id=access.user.id,
    )
    try:
        generated = await generate_protocol_elements(research_question, body.provider)
    except LLMError as exc:
        run.status, run.error = "failed", str(exc)
        db.add(run)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    run.status = "succeeded"
    db.add(run)
    # Regenerating replaces suggestions nobody has acted on; accepted and rejected criteria are kept.
    db.execute(
        delete(models.Criterion).where(models.Criterion.project_id == project.id, models.Criterion.status == "pending")
    )
    db.execute(delete(models.SearchStrategy).where(models.SearchStrategy.project_id == project.id))
    for kind, key in (("inclusion", "inclusion_criteria"), ("exclusion", "exclusion_criteria")):
        for text in generated[key]:
            db.add(models.Criterion(project_id=project.id, kind=kind, text=text[:2000], status="pending"))
    for search in generated["boolean_searches"]:
        db.add(
            models.SearchStrategy(
                project_id=project.id, database=str(search["database"])[:100], query=str(search["string"])
            )
        )
    added_fields = _add_extraction_fields(project, protocol.extraction_outline.splitlines())
    db.flush()

    record_event(
        db,
        project_id=project.id,
        actor_id=access.user.id,
        action="ai.protocol",
        entity_type="ai_run",
        entity_id=run.id,
        details={
            "provider": run.provider,
            "model": run.model,
            "criteria_suggested": len(generated["inclusion_criteria"]) + len(generated["exclusion_criteria"]),
            "search_strategies": len(generated["boolean_searches"]),
            "extraction_fields_added": added_fields,
        },
    )
    db.commit()
    return {
        "criteria": [criterion_out(c) for c in _project_criteria(db, project.id)],
        "search_strategies": [strategy_out(s) for s in _project_strategies(db, project.id)],
        "extraction_fields": [field.name for field in project.extraction_fields],
    }


@router.get("/criteria")
def list_criteria(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    return [criterion_out(c) for c in _project_criteria(db, access.project.id)]


@router.patch("/criteria/{criterion_id}")
def update_criterion(
    criterion_id: int,
    body: CriterionUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "protocol")
    criterion = get_in_project(db, models.Criterion, criterion_id, access.project.id, "Criterion")
    updates = body.model_dump(exclude_none=True)
    for name, value in updates.items():
        setattr(criterion, name, value)
    if updates:
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="criterion.updated",
            entity_type="criterion",
            entity_id=criterion.id,
            details=updates,
        )
    db.commit()
    return criterion_out(criterion)


@router.post("/criteria/accept-all")
def accept_all_criteria(
    body: AcceptAllRequest,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "protocol")
    pending = [c for c in _project_criteria(db, access.project.id) if c.kind == body.kind and c.status == "pending"]
    for criterion in pending:
        criterion.status = "accepted"
    if pending:
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="criteria.accepted_all",
            entity_type="criterion",
            details={"kind": body.kind, "criterion_ids": [c.id for c in pending]},
        )
    db.commit()
    return [criterion_out(c) for c in _project_criteria(db, access.project.id)]


@router.get("/search-strategies")
def list_search_strategies(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    return [strategy_out(s) for s in _project_strategies(db, access.project.id)]


@router.patch("/search-strategies/{strategy_id}")
def update_search_strategy(
    strategy_id: int,
    body: StrategyUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "protocol")
    strategy = get_in_project(db, models.SearchStrategy, strategy_id, access.project.id, "Search strategy")
    if strategy.query != body.query:
        record_event(
            db,
            project_id=access.project.id,
            actor_id=access.user.id,
            action="search_strategy.updated",
            entity_type="search_strategy",
            entity_id=strategy.id,
            details={"previous_query": strategy.query, "query": body.query},
        )
        strategy.query = body.query
    db.commit()
    return strategy_out(strategy)


@router.get("/extraction-fields")
def list_extraction_fields(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    return [field.name for field in access.project.extraction_fields]


@router.put("/extraction-fields")
def replace_extraction_fields(
    body: ExtractionFieldsUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "protocol")
    project = access.project
    names = _clean_field_names(body.names)
    before = [field.name for field in project.extraction_fields]
    removed = [name for name in before if name not in names]

    project.extraction_fields = [field for field in project.extraction_fields if field.name in names]
    _add_extraction_fields(project, names)
    order = {name: position for position, name in enumerate(names)}
    for field in project.extraction_fields:
        field.position = order[field.name]
    project.extraction_fields.sort(key=lambda field: field.position)

    added = [name for name in names if name not in before]
    if added or removed:
        record_event(
            db,
            project_id=project.id,
            actor_id=access.user.id,
            action="extraction_fields.updated",
            entity_type="project",
            entity_id=project.id,
            details={"added": added, "removed": removed},
        )
    db.commit()
    return [field.name for field in project.extraction_fields]
