"""Entity linking: mentions of conditions, interventions, drugs, outcomes, population characteristics, and tests in a
document, suggested by AI or added by reviewers, and linked to terminology codes (MeSH, RxNorm, ATC, ICD-11) once a
reviewer confirms them. AI mentions whose text can't be found in the document are dropped.
"""

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_access import new_ai_run, project_ai, record_usage
from audit import record_event
from database import get_db
from documents import document_passages, require_documents_open
from llm.grounding import locate_quote, select_passages
from llm.prompts import ENTITY_PROMPT
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from services.ai_screening import suggest_entities
from services.errors import LLMError
from services.terminologies import ONTOLOGY_LABELS, TerminologyError, icd11_configured, lookup

router = APIRouter(prefix="/api/projects/{project_id}", tags=["entities"])

EntityType = Literal["condition", "intervention", "drug", "outcome", "population", "test"]
ENTITY_TYPES: dict[str, tuple[str, tuple[str, ...]]] = {
    "condition": ("Condition or disease", ("mesh", "icd11")),
    "intervention": ("Intervention or exposure", ("mesh",)),
    "drug": ("Drug", ("rxnorm", "atc", "mesh")),
    "outcome": ("Outcome", ("mesh",)),
    "population": ("Population characteristic", ("mesh",)),
    "test": ("Diagnostic test or measurement", ("mesh",)),
}
MAX_ENTITY_TEXT_CHARS = 60_000
CANDIDATES_PER_TERMINOLOGY = 3


class EntityIn(BaseModel):
    span_id: int
    entity_type: EntityType
    text: str = Field(min_length=1, max_length=500)
    ontology: str = Field("", max_length=20)
    code: str = Field("", max_length=80)
    label: str = Field("", max_length=500)


class EntityUpdate(BaseModel):
    status: Literal["suggested", "confirmed", "rejected"] | None = None
    ontology: str | None = Field(None, max_length=20)
    code: str | None = Field(None, max_length=80)
    label: str | None = Field(None, max_length=500)


def _normalized(text: str) -> str:
    return " ".join(text.split()).casefold()


def entity_out(entity: models.DocumentEntity) -> dict:
    return {
        "id": entity.id,
        "document_id": entity.document_id,
        "span_id": entity.span_id,
        "entity_type": entity.entity_type,
        "text": entity.text,
        "ontology": entity.ontology,
        "code": entity.code,
        "label": entity.label,
        "candidates": entity.candidates,
        "status": entity.status,
        "source": entity.source,
        "reviewed_at": entity.reviewed_at,
    }


def lookup_candidates(entity_type: str, text: str) -> tuple[list[dict[str, str]], list[str]]:
    candidates: list[dict[str, str]] = []
    errors: list[str] = []
    for ontology in ENTITY_TYPES[entity_type][1]:
        if ontology == "icd11" and not icd11_configured():
            continue
        try:
            candidates.extend(lookup(ontology, text, CANDIDATES_PER_TERMINOLOGY))
        except TerminologyError as exc:
            errors.append(str(exc))
    return candidates, errors


def _document(db: Session, access: ProjectAccess, document_id: int) -> models.Document:
    document = get_in_project(db, models.Document, document_id, access.project.id, "Document")
    if document.parse_status != "parsed":
        raise HTTPException(status_code=409, detail="This document hasn't been read into passages")
    return document


def _entities(db: Session, document_id: int) -> list[models.DocumentEntity]:
    return list(
        db.scalars(
            select(models.DocumentEntity)
            .where(models.DocumentEntity.document_id == document_id)
            .order_by(models.DocumentEntity.span_id, models.DocumentEntity.id)
        )
    )


@router.post("/documents/{document_id}/entities/suggest", dependencies=[Depends(ai_rate_limit)])
async def suggest_document_entities(
    document_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    require_documents_open(db, access.project.id)
    document = _document(db, access, document_id)
    passages = select_passages(document_passages(db, [document.id])[document.id], MAX_ENTITY_TEXT_CHARS)
    ai = project_ai(db, access)
    run = new_ai_run(access, "entities", ENTITY_PROMPT, ai)
    run.record_id = document.record_id
    db.add(run)
    try:
        result = await suggest_entities(ai, passages)
    except LLMError as exc:
        run.status, run.error = "failed", str(exc)
        record_usage(run, ai, exc.usage)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    run.status = "succeeded"
    record_usage(run, ai, result.usage)
    db.flush()

    by_id = {passage.id: passage for passage in passages}
    known = {(e.entity_type, _normalized(e.text)) for e in _entities(db, document.id)}
    accepted, dropped = [], 0
    for mention in result.value:
        passage = by_id.get(mention.passage_id)
        if passage is not None and _normalized(mention.text) in _normalized(passage.text):
            span_id: int | None = passage.id
        else:
            span_id = locate_quote(mention.text, passages)
        if span_id is None:
            dropped += 1
            continue
        key = (mention.entity_type, _normalized(mention.text))
        if key in known:
            continue
        known.add(key)
        accepted.append((mention, span_id))

    lookups = await asyncio.to_thread(lambda: [lookup_candidates(m.entity_type, m.text) for m, _ in accepted])
    for (mention, span_id), (candidates, _) in zip(accepted, lookups, strict=True):
        first = candidates[0] if candidates else {}
        db.add(
            models.DocumentEntity(
                project_id=access.project.id,
                document_id=document.id,
                span_id=span_id,
                entity_type=mention.entity_type,
                text=mention.text[:500],
                ontology=first.get("ontology", ""),
                code=first.get("code", ""),
                label=first.get("label", "")[:500],
                candidates=candidates,
                status="suggested",
                source="ai",
                ai_run_id=run.id,
            )
        )
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="entities.suggested",
        entity_type="document",
        entity_id=document.id,
        details={"ai_run_id": run.id, "suggested": len(accepted), "dropped_unverified": dropped},
    )
    db.commit()
    return {
        "entities": [entity_out(e) for e in _entities(db, document.id)],
        "dropped_unverified": dropped,
        "lookup_errors": sorted({error for _, errors in lookups for error in errors}),
    }


@router.get("/documents/{document_id}/entities")
def list_entities(
    document_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    document = get_in_project(db, models.Document, document_id, access.project.id, "Document")
    return [entity_out(entity) for entity in _entities(db, document.id)]


@router.post("/documents/{document_id}/entities", status_code=201)
async def add_entity(
    document_id: int,
    body: EntityIn,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    """Add a mention by hand. With a code it's confirmed; without one, terminology candidates are looked up."""
    require_documents_open(db, access.project.id)
    document = _document(db, access, document_id)
    span = db.get(models.DocumentSpan, body.span_id)
    if span is None or span.document_id != document.id:
        raise HTTPException(status_code=404, detail="That passage isn't part of this document")
    if _normalized(body.text) not in _normalized(span.text):
        raise HTTPException(status_code=422, detail="The mention must be copied from the passage")
    confirmed = bool(body.ontology and body.code)
    if confirmed and body.ontology not in ONTOLOGY_LABELS:
        raise HTTPException(status_code=422, detail=f"Unknown terminology: {body.ontology}")
    candidates: list[dict[str, str]] = []
    if not confirmed:
        candidates, _ = await asyncio.to_thread(lookup_candidates, body.entity_type, body.text)
    first = candidates[0] if candidates else {}
    entity = models.DocumentEntity(
        project_id=access.project.id,
        document_id=document.id,
        span_id=span.id,
        entity_type=body.entity_type,
        text=body.text.strip(),
        ontology=body.ontology if confirmed else first.get("ontology", ""),
        code=body.code if confirmed else first.get("code", ""),
        label=(body.label if confirmed else first.get("label", ""))[:500],
        candidates=candidates,
        status="confirmed" if confirmed else "suggested",
        source="reviewer",
        reviewed_by_id=access.user.id if confirmed else None,
        reviewed_at=models.utcnow() if confirmed else None,
    )
    db.add(entity)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="entity.added",
        entity_type="document",
        entity_id=document.id,
        details={"entity_id": entity.id, "entity_type": entity.entity_type, "code": entity.code},
    )
    db.commit()
    return entity_out(entity)


@router.patch("/entities/{entity_id}")
def review_entity(
    entity_id: int,
    body: EntityUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EXTRACT)),
    db: Session = Depends(get_db),
):
    require_documents_open(db, access.project.id)
    entity = get_in_project(db, models.DocumentEntity, entity_id, access.project.id, "Entity")
    changes = body.model_dump(exclude_none=True)
    for name in ("ontology", "code", "label"):
        if name in changes:
            setattr(entity, name, changes[name].strip())
    if entity.ontology and entity.ontology not in ONTOLOGY_LABELS:
        raise HTTPException(status_code=422, detail=f"Unknown terminology: {entity.ontology}")
    if "status" in changes:
        if changes["status"] == "confirmed" and not (entity.ontology and entity.code):
            raise HTTPException(status_code=422, detail="Choose a terminology code before confirming")
        entity.status = changes["status"]
    entity.reviewed_by_id, entity.reviewed_at = access.user.id, models.utcnow()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="entity.reviewed",
        entity_type="document",
        entity_id=entity.document_id,
        details={"entity_id": entity.id, **changes},
    )
    db.commit()
    return entity_out(entity)


@router.get("/terminology/search")
async def search_terminology(
    ontology: str,
    q: str,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
):
    if ontology not in ONTOLOGY_LABELS:
        raise HTTPException(status_code=422, detail=f"Unknown terminology: {ontology}")
    if len(q.strip()) < 2:
        raise HTTPException(status_code=422, detail="Search for at least two characters")
    if ontology == "icd11" and not icd11_configured():
        raise HTTPException(
            status_code=409, detail="ICD-11 searches need ICD11_CLIENT_ID and ICD11_CLIENT_SECRET on the server"
        )
    try:
        return await asyncio.to_thread(lookup, ontology, q.strip(), 10)
    except TerminologyError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/entity-types")
def entity_types(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    return {
        "entity_types": [
            {"key": key, "label": label, "terminologies": list(o)} for key, (label, o) in ENTITY_TYPES.items()
        ],
        "terminologies": [
            {"key": key, "label": label, "available": key != "icd11" or icd11_configured()}
            for key, label in ONTOLOGY_LABELS.items()
        ],
    }
