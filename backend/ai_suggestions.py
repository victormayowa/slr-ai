"""Running an AI task for a project and storing its output as a suggestion a reviewer can accept.

Nothing in a project changes when the AI answers: the output is kept as a ProtocolSuggestion with the AI run that
produced it (provider, model, prompt version, tokens), and an audit event records that it was asked for. Failures are
recorded as failed runs, so what was attempted is visible even when a provider is down.
"""

from collections.abc import Awaitable, Callable
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

import models
from ai_access import new_ai_run, project_ai, record_usage
from audit import record_event
from llm.prompts import PromptTemplate
from llm.runner import AIContext, AIResult
from projects_routes import ProjectAccess
from services.errors import LLMError


async def run_suggestion(
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
