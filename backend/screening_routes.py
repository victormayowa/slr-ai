"""Reviewers' screening decisions and the AI narrative synthesis.

Batch AI suggestions (screening, extraction, appraisal) run as background jobs; see jobs_routes.py. AI output is stored
as suggestions with provenance; only a reviewer's decision includes or excludes a record.
"""

import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from ai_access import new_ai_run, project_ai, record_usage
from audit import record_event
from database import get_db
from llm.prompts import SYNTHESIS_PROMPT
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access
from rate_limiting import ai_rate_limit
from records_routes import record_out, with_record_details
from review_data import TITLE_ABSTRACT, final_decision
from services.ai_screening import (
    generate_narrative_synthesis,
)
from services.errors import LLMError
from workflow import require_stage_open

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/projects/{project_id}", tags=["screening"])


class DecisionRequest(BaseModel):
    decision: Literal["include", "exclude", "undecided"]


@router.put("/records/{record_id}/decision")
def set_screening_decision(
    record_id: int,
    body: DecisionRequest,
    access: ProjectAccess = Depends(project_access(Permission.SCREEN)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "screening")
    record = get_in_project(db, models.Record, record_id, access.project.id, "Record")
    if record.duplicate_of_id is not None:
        raise HTTPException(status_code=409, detail="This record is marked as a duplicate")

    existing = next(
        (d for d in record.decisions if d.stage == TITLE_ABSTRACT and d.reviewer_id == access.user.id), None
    )
    previous = existing.decision if existing else None
    if existing is not None:
        # Replace rather than update, so the newest decision always has the highest id.
        record.decisions.remove(existing)
        db.flush()
    record.decisions.append(
        models.ScreeningDecision(stage=TITLE_ABSTRACT, reviewer_id=access.user.id, decision=body.decision)
    )
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="screening.decided",
        entity_type="record",
        entity_id=record.id,
        details={"stage": TITLE_ABSTRACT, "decision": body.decision, "previous": previous},
    )
    db.commit()
    return record_out(record, access.user.id)


def synthesis_out(report: models.SynthesisReport) -> dict:
    return {
        "id": report.id,
        "content": report.content,
        "record_count": report.record_count,
        "provider": report.ai_run.provider,
        "model": report.ai_run.model,
        "created_at": report.created_at,
    }


@router.post("/synthesis", status_code=201, dependencies=[Depends(ai_rate_limit)])
async def create_synthesis(
    access: ProjectAccess = Depends(project_access(Permission.RUN_ANALYSIS)),
    db: Session = Depends(get_db),
):
    require_stage_open(db, access.project.id, "synthesis")
    records = db.scalars(
        with_record_details(
            select(models.Record).where(
                models.Record.project_id == access.project.id, models.Record.duplicate_of_id.is_(None)
            )
        )
    ).all()
    included = [record_out(record, access.user.id) for record in records if final_decision(record) == "include"]
    if not included:
        raise HTTPException(status_code=400, detail="Include at least one record before generating a synthesis")

    study_data = [
        {
            "title": record["title"],
            "authors": record["authors"],
            "year": record["year"],
            "doi": record["doi"],
            "extracted_data": record["extraction"]["values"] if record["extraction"] else None,
            "risk_of_bias": record["appraisal"]["judgments"] if record["appraisal"] else None,
        }
        for record in included
    ]
    ai = project_ai(db, access)
    run = new_ai_run(access, "synthesis", SYNTHESIS_PROMPT, ai)
    try:
        result = await generate_narrative_synthesis(ai, study_data)
    except LLMError as exc:
        run.status, run.error = "failed", str(exc)
        record_usage(run, ai, exc.usage)
        db.add(run)
        db.commit()
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    run.status = "succeeded"
    record_usage(run, ai, result.usage)
    report = models.SynthesisReport(
        project_id=access.project.id, ai_run=run, content=result.value, record_count=len(included)
    )
    db.add(report)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="ai.synthesis",
        entity_type="synthesis_report",
        entity_id=report.id,
        details={
            "provider": run.provider,
            "model": run.model,
            "prompt_version": run.prompt_version,
            "records": len(included),
        },
    )
    db.commit()
    return synthesis_out(report)


@router.get("/synthesis")
def latest_synthesis(
    access: ProjectAccess = Depends(project_access(Permission.VIEW_PROJECT)), db: Session = Depends(get_db)
):
    report = db.scalar(
        select(models.SynthesisReport)
        .where(models.SynthesisReport.project_id == access.project.id)
        .order_by(models.SynthesisReport.id.desc())
        .limit(1)
    )
    return synthesis_out(report) if report else None
