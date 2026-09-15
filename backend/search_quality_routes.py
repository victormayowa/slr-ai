"""Search strategy quality: versions, syntax checks, translation between databases, MeSH lookup, recall checks, and
PRESS peer review."""

import asyncio

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from audit import record_event
from auth_routes import get_current_user
from database import get_db
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from search_quality import (
    PRESS_KEYS,
    RECALL_CONNECTORS,
    found_seeds,
    parse_seeds,
    press_catalog,
    press_status,
    version_author,
)
from search_query import DATABASE_SYNTAXES, SYNTAXES, QuerySyntaxError, syntax_for_database, translate_pubmed, validate
from search_sources import connector_for
from services.errors import SearchError
from services.mesh import lookup_mesh
from workflow import WorkflowError, require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["search quality"])
vocabulary_router = APIRouter(
    prefix="/api/vocabulary", tags=["search quality"], dependencies=[Depends(get_current_user)]
)


class ValidateRequest(BaseModel):
    query: str = Field(max_length=20_000)
    syntax: str = Field(max_length=40)


class TranslateRequest(BaseModel):
    target: str = Field(max_length=40)


class RecallRequest(BaseModel):
    # PMIDs or DOIs of articles the search should find.
    seeds: list[str] = Field(min_length=1, max_length=50)
    connector: str | None = None


class PressAnswer(BaseModel):
    rating: str = Field(pattern="^(no_revision|revision_suggested|revision_required)$")
    comment: str = Field("", max_length=4000)


class PressReviewRequest(BaseModel):
    answers: dict[str, PressAnswer]
    comment: str = Field("", max_length=4000)


class PressWaiverRequest(BaseModel):
    reason: str = Field(min_length=20, max_length=2000)


@vocabulary_router.get("/mesh", dependencies=[Depends(ai_rate_limit)])
async def mesh_lookup(term: str = Query(min_length=2, max_length=200)):
    """MeSH headings for a term, with entry terms (synonyms), scope notes, and tree numbers."""
    try:
        return await asyncio.to_thread(lookup_mesh, term)
    except SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc


@router.get("/search-quality")
def search_quality_catalog(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    return {
        "syntaxes": [{"key": s.key, "label": s.label, "databases": s.databases} for s in SYNTAXES.values()],
        "press": press_catalog(),
        "recall_connectors": list(RECALL_CONNECTORS),
        # Normalized database names (lowercase words) mapped to syntax keys.
        "database_syntaxes": DATABASE_SYNTAXES,
    }


@router.post("/search-query/validate")
def validate_query(body: ValidateRequest, access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    if body.syntax not in SYNTAXES:
        raise HTTPException(status_code=422, detail=f"Unknown search syntax: {body.syntax}")
    return validate(body.query, body.syntax)


@router.get("/search-strategies/{strategy_id}/versions")
def strategy_versions(
    strategy_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    strategy = get_in_project(db, models.SearchStrategy, strategy_id, access.project.id, "Search strategy")
    versions = db.scalars(
        select(models.SearchStrategyVersion)
        .where(models.SearchStrategyVersion.strategy_id == strategy.id)
        .order_by(models.SearchStrategyVersion.version.desc())
    )
    return [
        {
            "version": v.version,
            "database": v.database,
            "query": v.query,
            "note": v.note,
            "created_by": v.created_by.full_name if v.created_by else None,
            "created_at": v.created_at,
        }
        for v in versions
    ]


@router.post("/search-strategies/{strategy_id}/translate")
def translate_strategy(
    strategy_id: int,
    body: TranslateRequest,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    """Render a PubMed strategy for another database, with warnings for what needs a searcher's attention."""
    strategy = get_in_project(db, models.SearchStrategy, strategy_id, access.project.id, "Search strategy")
    if syntax_for_database(strategy.database) != "pubmed":
        raise HTTPException(
            status_code=400, detail="Translation starts from a PubMed strategy. Translate the PubMed version instead."
        )
    if body.target not in SYNTAXES:
        raise HTTPException(status_code=422, detail=f"Unknown search syntax: {body.target}")
    try:
        query, warnings = translate_pubmed(strategy.query, body.target)
    except QuerySyntaxError as exc:
        raise HTTPException(status_code=400, detail=f"The PubMed strategy has a syntax error: {exc}") from exc
    return {"target": body.target, "target_label": SYNTAXES[body.target].label, "query": query, "warnings": warnings}


def recall_out(check: models.RecallCheck) -> dict:
    total = len(check.seeds)
    return {
        "id": check.id,
        "connector": check.connector,
        "strategy_version": check.strategy_version,
        "seeds": check.seeds,
        "found": check.found,
        "missed": [seed for seed in check.seeds if seed not in check.found],
        "recall": round(len(check.found) / total, 3) if total else None,
        "created_by": check.created_by.full_name if check.created_by else None,
        "created_at": check.created_at,
    }


@router.post("/search-strategies/{strategy_id}/recall-checks", status_code=201, dependencies=[Depends(ai_rate_limit)])
async def run_recall_check(
    strategy_id: int,
    body: RecallRequest,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    """Check which known relevant articles the strategy finds, as evidence that it's sensitive enough."""
    strategy = get_in_project(db, models.SearchStrategy, strategy_id, access.project.id, "Search strategy")
    connector = body.connector or (matched.key if (matched := connector_for(strategy.database)) else None)
    if connector not in RECALL_CONNECTORS:
        raise HTTPException(status_code=400, detail="Recall checks run on PubMed, Europe PMC, or OpenAlex strategies")
    pmids, dois, unrecognized = parse_seeds(body.seeds)
    if unrecognized:
        raise HTTPException(status_code=422, detail=f"Use PMIDs or DOIs. Not recognized: {', '.join(unrecognized[:5])}")
    try:
        found = await asyncio.to_thread(found_seeds, connector, strategy.query, pmids, dois)
    except SearchError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    seeds = pmids + dois
    check = models.RecallCheck(
        project_id=access.project.id,
        strategy_id=strategy.id,
        strategy_version=strategy.version,
        connector=connector,
        seeds=seeds,
        found=[seed for seed in seeds if seed in found],
        created_by_id=access.user.id,
    )
    db.add(check)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="search_strategy.recall_checked",
        entity_type="search_strategy",
        entity_id=strategy.id,
        details={"version": strategy.version, "connector": connector, "seeds": len(seeds), "found": len(check.found)},
    )
    db.commit()
    return recall_out(check)


@router.get("/search-strategies/{strategy_id}/recall-checks")
def list_recall_checks(
    strategy_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    strategy = get_in_project(db, models.SearchStrategy, strategy_id, access.project.id, "Search strategy")
    checks = db.scalars(
        select(models.RecallCheck)
        .where(models.RecallCheck.strategy_id == strategy.id)
        .order_by(models.RecallCheck.id.desc())
    )
    return [recall_out(check) for check in checks]


def press_review_out(review: models.PressReview) -> dict:
    return {
        "id": review.id,
        "strategy_id": review.strategy_id,
        "strategy_version": review.strategy_version,
        "status": review.status,
        "answers": review.answers,
        "overall": review.overall,
        "comment": review.comment,
        "reviewer": review.reviewer.full_name if review.reviewer else None,
        "created_at": review.created_at,
    }


def _require_protocol_or_search_open(db: Session, project_id: int) -> None:
    try:
        require_stage_open(db, project_id, "protocol")
    except WorkflowError:
        require_stage_open(db, project_id, "search")


@router.get("/press-status")
def get_press_status(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    return press_status(db, access.project.id)


@router.get("/search-strategies/{strategy_id}/press-reviews")
def list_press_reviews(
    strategy_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    strategy = get_in_project(db, models.SearchStrategy, strategy_id, access.project.id, "Search strategy")
    reviews = db.scalars(
        select(models.PressReview)
        .where(models.PressReview.strategy_id == strategy.id)
        .order_by(models.PressReview.id.desc())
    )
    return [press_review_out(review) for review in reviews]


@router.post("/search-strategies/{strategy_id}/press-reviews", status_code=201)
def submit_press_review(
    strategy_id: int,
    body: PressReviewRequest,
    access: ProjectAccess = Depends(project_access(Permission.RUN_SEARCH)),
    db: Session = Depends(get_db),
):
    """Record a PRESS 2015 peer review of the strategy's current version by someone other than its author."""
    _require_protocol_or_search_open(db, access.project.id)
    strategy = get_in_project(db, models.SearchStrategy, strategy_id, access.project.id, "Search strategy")
    missing = [key for key in PRESS_KEYS if key not in body.answers]
    unknown = [key for key in body.answers if key not in PRESS_KEYS]
    if missing or unknown:
        raise HTTPException(status_code=422, detail="Answer each of the six PRESS elements, and only those")
    if version_author(db, strategy) == access.user.id:
        raise HTTPException(
            status_code=409, detail="Someone other than the author of this strategy version must peer review it"
        )
    answers = {key: body.answers[key].model_dump() for key in PRESS_KEYS}
    overall = (
        "revisions_required"
        if any(answer["rating"] == "revision_required" for answer in answers.values())
        else "approved"
    )
    review = models.PressReview(
        project_id=access.project.id,
        strategy_id=strategy.id,
        strategy_version=strategy.version,
        reviewer_id=access.user.id,
        status="completed",
        answers=answers,
        overall=overall,
        comment=body.comment.strip(),
    )
    db.add(review)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="search_strategy.press_reviewed",
        entity_type="search_strategy",
        entity_id=strategy.id,
        details={"version": strategy.version, "overall": overall},
    )
    db.commit()
    return press_review_out(review)


@router.post("/press-waiver", status_code=201)
def waive_press(
    body: PressWaiverRequest,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Record that search strategies won't be peer reviewed with PRESS, and why."""
    require_stage_open(db, access.project.id, "search")
    review = models.PressReview(
        project_id=access.project.id, reviewer_id=access.user.id, status="waived", comment=body.reason.strip()
    )
    db.add(review)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="search.press_waived",
        entity_type="project",
        entity_id=access.project.id,
        details={"reason": review.comment},
    )
    db.commit()
    return press_review_out(review)
