"""Protocol design: the structured review question with its FINER assessment, the pre-specified analysis plan, the
PRISMA-P protocol document, and protocol checks.

AI output here is stored as suggestions (ProtocolSuggestion); nothing in the protocol changes until a reviewer saves.
"""

import hashlib
from collections.abc import Awaitable, Callable
from dataclasses import asdict
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, ValidationError
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_access import new_ai_run, project_ai, record_usage
from audit import record_event
from auth_routes import get_current_user
from database import get_db
from llm.prompts import CONSISTENCY_PROMPT, QUESTION_PROMPT, SECTION_DRAFT_PROMPT, PromptTemplate
from llm.runner import AIContext, AIResult
from permissions import Permission
from projects_routes import ProjectAccess, project_access
from protocol_design import project_criteria, project_sections, protocol_context, protocol_issues
from protocol_frameworks import FINER_CRITERIA, FRAMEWORKS, PROTOCOL_SECTIONS, SECTIONS_BY_KEY, catalog
from rate_limiting import ai_rate_limit
from services.ai_protocol_design import draft_protocol_section, review_protocol_consistency, structure_question
from services.errors import LLMError
from workflow import require_stage_open

router = APIRouter(prefix="/api/projects/{project_id}", tags=["protocol design"])
catalog_router = APIRouter(prefix="/api", tags=["protocol design"], dependencies=[Depends(get_current_user)])

MAX_ELEMENT_LENGTH = 2000


@catalog_router.get("/protocol-frameworks")
def protocol_frameworks():
    """Question frameworks, FINER criteria, PRISMA-P sections, and analysis plan options."""
    return catalog()


class FinerAssessment(BaseModel):
    rating: Literal["yes", "partly", "no"]
    note: str = Field("", max_length=2000)


class QuestionUpdate(BaseModel):
    framework: str = Field(max_length=20)
    question: str = Field(max_length=2000)
    elements: dict[str, str] = Field(default_factory=dict)
    finer: dict[str, FinerAssessment] = Field(default_factory=dict)


class PlannedOutcome(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    priority: Literal["primary", "secondary", "adverse"]
    timepoint: str = Field("", max_length=200)
    measure: str = Field("", max_length=300)


class PlannedAnalysis(BaseModel):
    name: str = Field(min_length=1, max_length=300)
    rationale: str = Field("", max_length=2000)


class AnalysisPlan(BaseModel):
    synthesis_approach: Literal["meta_analysis", "swim", "narrative", "undecided"] = "undecided"
    outcomes: list[PlannedOutcome] = Field(default_factory=list, max_length=50)
    subgroups: list[PlannedAnalysis] = Field(default_factory=list, max_length=30)
    sensitivity_analyses: list[PlannedAnalysis] = Field(default_factory=list, max_length=30)
    heterogeneity: str = Field("", max_length=5000)


class SectionUpdate(BaseModel):
    content: str = Field(max_length=50_000)
    # The AI draft this text was accepted from, recorded as provenance.
    based_on_draft_id: int | None = None


def _protocol(access: ProjectAccess) -> models.Protocol:
    if access.project.protocol is None:
        raise HTTPException(status_code=404, detail="This project has no protocol")
    return access.project.protocol


def question_out(protocol: models.Protocol) -> dict:
    framework = FRAMEWORKS.get(protocol.framework)
    elements = protocol.question_elements or {}
    return {
        "framework": protocol.framework,
        "question": protocol.question,
        "elements": {e.key: elements.get(e.key, "") for e in framework.elements} if framework else elements,
        "finer": protocol.finer or {},
    }


def analysis_plan_out(protocol: models.Protocol) -> dict:
    try:
        return AnalysisPlan.model_validate(protocol.analysis_plan or {}).model_dump()
    except ValidationError:
        return AnalysisPlan.model_validate({}).model_dump()


def suggestion_out(suggestion: models.ProtocolSuggestion) -> dict:
    return {
        "id": suggestion.id,
        "kind": suggestion.kind,
        "section_key": suggestion.section_key,
        "content": suggestion.content,
        "provider": suggestion.ai_run.provider,
        "model": suggestion.ai_run.model,
        "created_at": suggestion.created_at,
    }


def _latest_suggestions(db: Session, project_id: int, kind: str) -> list[models.ProtocolSuggestion]:
    return list(
        db.scalars(
            select(models.ProtocolSuggestion)
            .where(models.ProtocolSuggestion.project_id == project_id, models.ProtocolSuggestion.kind == kind)
            .order_by(models.ProtocolSuggestion.id)
        )
    )


def _record_change(db: Session, access: ProjectAccess, action: str, entity_id: int, details: dict[str, Any]) -> None:
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=action,
        entity_type="protocol",
        entity_id=entity_id,
        details=details,
    )


async def run_protocol_ai(
    db: Session,
    access: ProjectAccess,
    task: str,
    prompt: PromptTemplate,
    call: Callable[[AIContext], Awaitable[AIResult[dict[str, Any]]]],
    section_key: str | None = None,
) -> models.ProtocolSuggestion:
    """Run an AI protocol task and store its output as a suggestion, recording failures as failed runs."""
    ai = project_ai(db, access)
    run = new_ai_run(access, task, prompt, ai)
    try:
        result = await call(ai)
    except LLMError as exc:
        run.status, run.error = "failed", str(exc)
        record_usage(run, ai, exc.usage)
        db.add(run)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    run.status = "succeeded"
    record_usage(run, ai, result.usage)
    suggestion = models.ProtocolSuggestion(
        project_id=access.project.id, ai_run=run, kind=task, section_key=section_key, content=result.value
    )
    db.add(suggestion)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action=f"ai.{task}",
        entity_type="protocol_suggestion",
        entity_id=suggestion.id,
        details={
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "key_source": run.key_source,
            "section": section_key,
        },
    )
    db.commit()
    return suggestion


# Review question


@router.get("/question")
def get_question(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    return question_out(_protocol(access))


@router.put("/question")
def update_question(
    body: QuestionUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "protocol")
    protocol = _protocol(access)
    framework = FRAMEWORKS.get(body.framework)
    if framework is None:
        raise HTTPException(status_code=422, detail=f"Unknown question framework: {body.framework}")
    element_keys = [element.key for element in framework.elements]
    unknown = sorted(set(body.elements) - set(element_keys))
    if unknown:
        raise HTTPException(status_code=422, detail=f"{framework.label} has no element named {', '.join(unknown)}")
    if any(len(text) > MAX_ELEMENT_LENGTH for text in body.elements.values()):
        raise HTTPException(status_code=422, detail=f"Question elements can be at most {MAX_ELEMENT_LENGTH} characters")
    unknown_finer = sorted(set(body.finer) - {criterion.key for criterion in FINER_CRITERIA})
    if unknown_finer:
        raise HTTPException(status_code=422, detail=f"Unknown FINER criteria: {', '.join(unknown_finer)}")

    updated = {
        "framework": framework.key,
        "question": body.question.strip(),
        "question_elements": {key: body.elements.get(key, "").strip() for key in element_keys},
        "finer": {
            criterion.key: body.finer[criterion.key].model_dump()
            for criterion in FINER_CRITERIA
            if criterion.key in body.finer
        },
    }
    changes = {name: value for name, value in updated.items() if getattr(protocol, name) != value}
    for name, value in changes.items():
        setattr(protocol, name, value)
    if changes:
        _record_change(db, access, "question.updated", protocol.id, {"changes": changes})
    db.commit()
    return question_out(protocol)


@router.post("/question/ai", dependencies=[Depends(ai_rate_limit)])
async def suggest_question(
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)), db: Session = Depends(get_db)
):
    """Suggest a framework, question, and elements from the study description. Reviewers apply it by saving."""
    require_stage_open(db, access.project.id, "protocol")
    protocol = _protocol(access)
    if not protocol.description.strip():
        raise HTTPException(status_code=400, detail="Describe the study in Project Setup first")
    topic = "\n".join(
        [
            f"Title: {access.project.title}",
            f"Review type: {protocol.review_type}",
            f"Description: {protocol.description}",
            f"Suggested criteria: {protocol.suggested_criteria}",
        ]
    )
    suggestion = await run_protocol_ai(
        db, access, "question", QUESTION_PROMPT, lambda ai: structure_question(ai, topic)
    )
    return suggestion_out(suggestion)


# Analysis plan


@router.get("/analysis-plan")
def get_analysis_plan(access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT))):
    return analysis_plan_out(_protocol(access))


@router.put("/analysis-plan")
def update_analysis_plan(
    body: AnalysisPlan,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    """Pre-specify outcomes, subgroup and sensitivity analyses, and the synthesis approach before the protocol locks."""
    require_stage_open(db, access.project.id, "protocol")
    protocol = _protocol(access)
    plan = body.model_dump()
    if plan != analysis_plan_out(protocol):
        protocol.analysis_plan = plan
        _record_change(db, access, "analysis_plan.updated", protocol.id, {"plan": plan})
    db.commit()
    return analysis_plan_out(protocol)


# Protocol document


def _section_out(
    key: str, row: models.ProtocolSection | None, draft: models.ProtocolSuggestion | None
) -> dict[str, Any]:
    section = SECTIONS_BY_KEY[key]
    return {
        **asdict(section),
        "content": row.content if row else "",
        "ai_assisted": bool(row and row.based_on_suggestion_id),
        "updated_at": row.updated_at if row else None,
        "updated_by": row.updated_by.full_name if row and row.updated_by else None,
        "draft": suggestion_out(draft) if draft else None,
    }


@router.get("/protocol-sections")
def list_protocol_sections(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    """Every PRISMA-P section with its saved text and the latest AI draft, if any."""
    rows = project_sections(db, access.project.id)
    drafts = {
        suggestion.section_key: suggestion for suggestion in _latest_suggestions(db, access.project.id, "section")
    }
    return [_section_out(section.key, rows.get(section.key), drafts.get(section.key)) for section in PROTOCOL_SECTIONS]


@router.put("/protocol-sections/{key}")
def update_protocol_section(
    key: str,
    body: SectionUpdate,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    if key not in SECTIONS_BY_KEY:
        raise HTTPException(status_code=404, detail="Unknown protocol section")
    require_stage_open(db, access.project.id, "protocol")
    draft = None
    if body.based_on_draft_id is not None:
        draft = db.get(models.ProtocolSuggestion, body.based_on_draft_id)
        if draft is None or draft.project_id != access.project.id or draft.section_key != key:
            raise HTTPException(status_code=404, detail="AI draft not found for this section")

    row = project_sections(db, access.project.id).get(key)
    content = body.content.strip()
    if row is None:
        row = models.ProtocolSection(project_id=access.project.id, key=key, content="")
        db.add(row)
    if content != row.content or draft is not None:
        row.content = content
        row.updated_by_id, row.updated_at = access.user.id, models.utcnow()
        if not content:
            row.based_on_suggestion_id = None
        elif draft is not None:
            row.based_on_suggestion_id = draft.id
        db.flush()
        _record_change(
            db,
            access,
            "protocol_section.updated",
            access.project.protocol.id if access.project.protocol else access.project.id,
            {
                "section": key,
                "characters": len(content),
                "content_sha256": hashlib.sha256(content.encode()).hexdigest(),
                "based_on_draft_id": draft.id if draft else None,
            },
        )
    db.commit()
    drafts = _latest_suggestions(db, access.project.id, "section")
    return _section_out(key, row, next((d for d in reversed(drafts) if d.section_key == key), None))


@router.post("/protocol-sections/{key}/ai-draft", dependencies=[Depends(ai_rate_limit)])
async def draft_section(
    key: str,
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)),
    db: Session = Depends(get_db),
):
    """Draft a section from what the protocol already says, with placeholders for anything missing."""
    section = SECTIONS_BY_KEY.get(key)
    if section is None:
        raise HTTPException(status_code=404, detail="Unknown protocol section")
    require_stage_open(db, access.project.id, "protocol")
    context = protocol_context(db, access.project, exclude_section=key)
    suggestion = await run_protocol_ai(
        db,
        access,
        "section",
        SECTION_DRAFT_PROMPT,
        lambda ai: draft_protocol_section(ai, section, context),
        section_key=key,
    )
    return suggestion_out(suggestion)


# Checks


@router.get("/protocol-checks")
def get_protocol_checks(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    reviews = _latest_suggestions(db, access.project.id, "consistency")
    return {
        "issues": [asdict(issue) for issue in protocol_issues(db, access.project)],
        "ai_review": suggestion_out(reviews[-1]) if reviews else None,
    }


@router.post("/protocol-checks/ai", dependencies=[Depends(ai_rate_limit)])
async def review_consistency(
    access: ProjectAccess = Depends(project_access(Permission.EDIT_PROTOCOL)), db: Session = Depends(get_db)
):
    """Ask the AI to look for contradictions between the question, criteria, and analysis plan."""
    require_stage_open(db, access.project.id, "protocol")
    protocol = _protocol(access)
    accepted = [c.id for c in project_criteria(db, access.project.id) if c.status == "accepted"]
    if not accepted:
        raise HTTPException(status_code=400, detail="Accept at least one criterion before checking consistency")
    framework = FRAMEWORKS.get(protocol.framework)
    element_keys = {element.key for element in framework.elements} if framework else set()
    context = protocol_context(db, access.project)
    suggestion = await run_protocol_ai(
        db,
        access,
        "consistency",
        CONSISTENCY_PROMPT,
        lambda ai: review_protocol_consistency(ai, context, set(accepted), element_keys),
    )
    return suggestion_out(suggestion)
