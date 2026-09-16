"""Chooses the model and API key for each AI call, and records what each call used.

A user's own saved key for the provider is used first, then the server's key for that provider.
"""

import logging
from decimal import Decimal

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import crypto
import entitlements
import models
from llm.prompts import PromptTemplate
from llm.providers import PROVIDERS
from llm.runner import AIContext, Usage
from projects_routes import ProjectAccess

logger = logging.getLogger(__name__)

_MILLION = Decimal(1_000_000)


def api_key_context(user_id: int, provider: str) -> str:
    return f"user_api_key:{user_id}:{provider}"


def user_api_key(db: Session, user_id: int, provider: str) -> models.UserAPIKey | None:
    return db.scalar(
        select(models.UserAPIKey).where(models.UserAPIKey.user_id == user_id, models.UserAPIKey.provider == provider)
    )


def decrypt_user_key(row: models.UserAPIKey) -> str:
    label = PROVIDERS[row.provider].label if row.provider in PROVIDERS else row.provider
    try:
        return crypto.decrypt(row.encrypted_key, api_key_context(row.user_id, row.provider))
    except crypto.EncryptionNotConfigured as exc:
        logger.error("DATA_ENCRYPTION_KEY is not configured, so saved API keys can't be decrypted")
        raise HTTPException(
            status_code=503, detail="Saved API keys can't be used because the server's encryption isn't configured"
        ) from exc
    except InvalidTag as exc:
        logger.error("Saved %s API key for user %s failed to decrypt", row.provider, row.user_id)
        raise HTTPException(
            status_code=409, detail=f"Your saved {label} key can't be read any more. Save it again in Settings."
        ) from exc


def resolve_ai(db: Session, model: models.AIModel | None, user: models.User) -> AIContext:
    if model is None:
        raise HTTPException(status_code=409, detail="Choose an AI model for this project in Project Setup first")
    spec = PROVIDERS.get(model.provider)
    if not model.enabled or spec is None:
        raise HTTPException(
            status_code=409,
            detail=f'The AI model "{model.label}" is no longer offered. Choose another model in Project Setup.',
        )

    prices = {"input_price_per_mtok": model.input_price_per_mtok, "output_price_per_mtok": model.output_price_per_mtok}
    saved = user_api_key(db, user.id, spec.id)
    if saved is not None:
        return AIContext(spec, model.model_id, decrypt_user_key(saved), "user", **prices)
    platform_key = spec.platform_api_key()
    if platform_key is not None:
        return AIContext(spec, model.model_id, platform_key, "platform", **prices)
    raise HTTPException(
        status_code=400,
        detail=f"No {spec.label} API key is available. Add your own key in Settings, or choose a model from a "
        "provider this server is configured for.",
    )


def project_ai(db: Session, access: ProjectAccess) -> AIContext:
    ai = resolve_ai(db, access.project.ai_model, access.user)
    if ai.key_source == "platform":
        entitlements.require_ai(db, access.project, ai.provider.id)
    return ai


def project_embedding_ai(db: Session, access: ProjectAccess) -> AIContext:
    if access.project.embedding_model is None:
        raise HTTPException(status_code=409, detail="Choose an embedding model for this project first")
    ai = resolve_ai(db, access.project.embedding_model, access.user)
    if ai.key_source == "platform":
        entitlements.require_ai(db, access.project, ai.provider.id)
    return ai


def new_ai_run(access: ProjectAccess, task: str, prompt: PromptTemplate, ai: AIContext) -> models.AIRun:
    return models.AIRun(
        project_id=access.project.id,
        task=task,
        provider=ai.provider.id,
        model=ai.model,
        prompt_version=prompt.id,
        key_source=ai.key_source,
        triggered_by_id=access.user.id,
    )


def record_usage(run: models.AIRun, ai: AIContext, usage: Usage | None) -> None:
    if usage is None:
        return
    run.input_tokens = usage.input_tokens
    run.output_tokens = usage.output_tokens
    run.latency_ms = usage.latency_ms
    run.attempts = usage.attempts
    if (
        usage.input_tokens is not None
        and usage.output_tokens is not None
        and ai.input_price_per_mtok is not None
        and ai.output_price_per_mtok is not None
    ):
        cost = (
            usage.input_tokens * ai.input_price_per_mtok + usage.output_tokens * ai.output_price_per_mtok
        ) / _MILLION
        run.cost_usd = cost.quantize(Decimal("0.000001"))
