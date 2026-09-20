"""Topic exploration before writing the protocol: publication counts and trends, existing reviews and registrations,
feasibility estimates, and AI-suggested questions based on the gaps found."""

from dataclasses import asdict

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_suggestions import run_suggestion, suggestion_out
from audit import record_event
from database import get_db
from llm.prompts import TOPIC_QUESTIONS_PROMPT
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from services.ai_protocol_design import suggest_topic_questions
from topic_exploration import WorkloadAssumptions, explore_topic

router = APIRouter(prefix="/api/projects/{project_id}", tags=["topic exploration"])


class ExplorationRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    reviewers: int = Field(2, ge=1, le=10)
    minutes_per_abstract: float = Field(0.5, gt=0, le=30)
    full_text_fraction: float = Field(0.05, ge=0, le=1)
    minutes_per_full_text: float = Field(5, gt=0, le=240)
    include_fraction: float = Field(0.3, ge=0, le=1)
    hours_per_included_study: float = Field(1.5, gt=0, le=40)


def _question_suggestions(db: Session, project_id: int, exploration_id: int) -> list[models.ProtocolSuggestion]:
    suggestions = db.scalars(
        select(models.ProtocolSuggestion)
        .where(models.ProtocolSuggestion.project_id == project_id, models.ProtocolSuggestion.kind == "topic_questions")
        .order_by(models.ProtocolSuggestion.id)
    )
    return [s for s in suggestions if s.content.get("exploration_id") == exploration_id]


def exploration_out(db: Session, exploration: models.TopicExploration) -> dict:
    suggestions = _question_suggestions(db, exploration.project_id, exploration.id)
    return {
        "id": exploration.id,
        "query": exploration.query,
        "assumptions": exploration.assumptions,
        "results": exploration.results,
        "created_by": exploration.created_by.full_name if exploration.created_by else None,
        "created_at": exploration.created_at,
        "ai_questions": suggestion_out(suggestions[-1]) if suggestions else None,
    }


@router.post("/topic-explorations", status_code=201, dependencies=[Depends(ai_rate_limit)])
async def explore(
    body: ExplorationRequest,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    """Search PubMed, OpenAlex, ClinicalTrials.gov, and OSF Registries for the terms, and store what was found."""
    assumptions = WorkloadAssumptions(**body.model_dump(exclude={"query"}))
    query = body.query.strip()
    results = await explore_topic(query, assumptions)
    exploration = models.TopicExploration(
        project_id=access.project.id,
        query=query,
        assumptions=asdict(assumptions),
        results=results,
        created_by_id=access.user.id,
    )
    db.add(exploration)
    db.flush()
    failed = [source["label"] for source in results["sources"].values() if source["error"]]
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="topic.explored",
        entity_type="topic_exploration",
        entity_id=exploration.id,
        details={"query": query, "reviews_found": len(results["existing_reviews"]), "sources_failed": failed},
    )
    db.commit()
    return exploration_out(db, exploration)


@router.get("/topic-explorations")
def list_explorations(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    explorations = db.scalars(
        select(models.TopicExploration)
        .where(models.TopicExploration.project_id == access.project.id)
        .order_by(models.TopicExploration.id.desc())
        .limit(20)
    )
    return [
        {
            "id": e.id,
            "query": e.query,
            "created_at": e.created_at,
            "created_by": e.created_by.full_name if e.created_by else None,
        }
        for e in explorations
    ]


@router.get("/topic-explorations/{exploration_id}")
def get_exploration(
    exploration_id: int,
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)),
    db: Session = Depends(get_db),
):
    exploration = get_in_project(db, models.TopicExploration, exploration_id, access.project.id, "Topic exploration")
    return exploration_out(db, exploration)


@router.post("/topic-explorations/{exploration_id}/ai-questions", dependencies=[Depends(ai_rate_limit)])
async def suggest_questions(
    exploration_id: int,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    """Suggest review questions that address gaps in the retrieved evidence. Nothing changes until a reviewer saves."""
    exploration = get_in_project(db, models.TopicExploration, exploration_id, access.project.id, "Topic exploration")
    protocol = access.project.protocol
    if protocol is None:
        raise HTTPException(status_code=404, detail="This project has no protocol")
    results = exploration.results
    reviews = results.get("existing_reviews", [])
    project = {
        "title": access.project.title,
        "review_type": protocol.review_type,
        "study_description": protocol.description,
        "current_review_question": protocol.question,
    }
    evidence = {
        "search_terms": exploration.query,
        "counts": {source["label"]: source["count"] for source in results.get("sources", {}).values()},
        "publications_by_year": results.get("publications_by_year", {}),
        "existing_reviews": [
            {
                "id": review["ref"],
                "title": review["title"],
                "year": review["year"],
                "source": review["source"],
                "possibly_outdated": review.get("possibly_outdated"),
                "newer_randomized_trials": review.get("newer_randomized_trials"),
            }
            for review in reviews
        ],
        "registered_protocols": [
            {"title": r["title"], "registered": r["registered"]} for r in results.get("registrations", [])
        ],
        "meta_analysis_feasibility": results.get("meta_analysis_feasibility", {}).get("level"),
    }
    review_ids = {review["ref"] for review in reviews}

    async def call(ai):
        result = await suggest_topic_questions(ai, project, evidence, review_ids)
        result.value["exploration_id"] = exploration.id
        return result

    suggestion = await run_suggestion(db, access, "topic_questions", TOPIC_QUESTIONS_PROMPT, call)
    return suggestion_out(suggestion)
