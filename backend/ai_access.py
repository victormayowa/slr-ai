"""Chooses the model and API key for each AI call, and records what each call used.

Each user chooses (User.ai_key_mode): "auto" uses their own saved key for the provider first, then the server's key;
"own" uses only their own keys; "platform" uses only the server's keys, which count against the plan's AI credits.
AI_PLATFORM_KEYS=false turns the server's keys off for everyone.
"""

import logging
from collections.abc import Collection
from decimal import Decimal

from cryptography.exceptions import InvalidTag
from fastapi import HTTPException
from sqlalchemy import select
from sqlalchemy.orm import Session

import ai_credit
import crypto
import entitlements
import models
from llm.prompts import PromptTemplate
from llm.providers import PROVIDERS, platform_keys_enabled
from llm.runner import AIContext, Usage
from projects_routes import ProjectAccess

logger = logging.getLogger(__name__)

_MILLION = Decimal(1_000_000)
AI_KEY_MODES = ("auto", "own", "platform")


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
    mode = user.ai_key_mode if user.ai_key_mode in AI_KEY_MODES else "auto"
    if mode != "platform":
        saved = user_api_key(db, user.id, spec.id)
        if saved is not None:
            return AIContext(spec, model.model_id, decrypt_user_key(saved), "user", **prices)
    platform_key = spec.platform_api_key() if mode != "own" else None
    if platform_key is not None:
        return AIContext(spec, model.model_id, platform_key, "platform", **prices)
    if not platform_keys_enabled():
        detail = f"This server uses your own AI provider keys only. Add a {spec.label} key in Settings."
    elif mode == "own":
        detail = (
            f"You chose to use only your own API keys, and you haven't added a {spec.label} key. Add one in "
            "Settings, or let AI tasks use the plan's included AI."
        )
    elif mode == "platform":
        detail = (
            f"You chose to use only the plan's included AI, and this server has no {spec.label} key. Choose a model "
            "from another provider, or allow your own keys in Settings."
        )
    else:
        detail = (
            f"No {spec.label} API key is available. Add your own key in Settings, or choose a model from a "
            "provider this server is configured for."
        )
    raise HTTPException(status_code=400, detail=detail)


def key_available(saved_providers: Collection[str], provider: str, user: models.User) -> bool:
    """Whether resolve_ai would find a key for the provider, given the user's saved keys and choice."""
    spec = PROVIDERS[provider]
    if user.ai_key_mode != "platform" and provider in saved_providers:
        return True
    return user.ai_key_mode != "own" and spec.platform_api_key() is not None


def project_ai(db: Session, access: ProjectAccess) -> AIContext:
    model = access.project.ai_model
    ai = resolve_ai(db, model, access.user)
    if ai.key_source == "platform" and model is not None:
        entitlements.require_ai(db, access.project, ai.provider.id)
        ai_credit.require_balance(db, access.project, model)
    return ai


def project_embedding_ai(db: Session, access: ProjectAccess) -> AIContext:
    if access.project.embedding_model is None:
        raise HTTPException(status_code=409, detail="Choose an embedding model for this project first")
    model = access.project.embedding_model
    ai = resolve_ai(db, model, access.user)
    if ai.key_source == "platform":
        entitlements.require_ai(db, access.project, ai.provider.id)
        ai_credit.require_balance(db, access.project, model)
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
