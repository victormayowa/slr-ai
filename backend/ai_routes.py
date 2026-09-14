"""AI providers, the model catalog, and users' own provider API keys.

Saved keys are encrypted (crypto.py) and never sent back to the browser; only their last four characters are shown.
"""

import asyncio
import logging
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import crypto
import models
from ai_access import api_key_context, decrypt_user_key
from ai_catalog import ai_model_out, catalog_models
from auth_routes import get_current_user
from database import get_db
from llm import adapters
from llm.providers import PROVIDERS, ProviderSpec
from rate_limiting import ai_rate_limit

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api", tags=["ai"])

KEY_TEST_TIMEOUT_SECONDS = 30


class APIKeyUpdate(BaseModel):
    api_key: str = Field(min_length=8, max_length=500)


def _provider(provider_id: str) -> ProviderSpec:
    spec = PROVIDERS.get(provider_id)
    if spec is None:
        raise HTTPException(status_code=404, detail="Unknown AI provider")
    return spec


def _saved_keys(db: Session, user: models.User) -> dict[str, models.UserAPIKey]:
    rows = db.scalars(select(models.UserAPIKey).where(models.UserAPIKey.user_id == user.id))
    return {row.provider: row for row in rows}


def _key_out(row: models.UserAPIKey) -> dict:
    return {
        "provider": row.provider,
        "last_four": row.last_four,
        "updated_at": row.updated_at,
        "last_verified_at": row.last_verified_at,
    }


@router.get("/ai/providers")
def list_providers(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    saved = _saved_keys(db, user)
    return [
        {
            "id": spec.id,
            "label": spec.label,
            "data_location": spec.headquarters,
            "platform_key_configured": spec.platform_api_key() is not None,
            "user_key": _key_out(saved[spec.id]) if spec.id in saved else None,
        }
        for spec in PROVIDERS.values()
    ]


@router.get("/ai/models")
def list_models(
    purpose: Literal["chat", "embedding"] = "chat",
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Enabled catalog models. `available` says whether the caller has a key (their own or the server's) to use it."""
    saved = _saved_keys(db, user)
    return [
        {
            **ai_model_out(model),
            "available": model.provider in saved or PROVIDERS[model.provider].platform_api_key() is not None,
        }
        for model in catalog_models(db, purpose)
    ]


@router.get("/me/api-keys")
def list_api_keys(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    return [_key_out(row) for row in _saved_keys(db, user).values()]


@router.put("/me/api-keys/{provider}")
def save_api_key(
    provider: str,
    body: APIKeyUpdate,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    spec = _provider(provider)
    api_key = body.api_key.strip()
    if len(api_key) < 8 or any(character.isspace() for character in api_key):
        raise HTTPException(status_code=422, detail="That doesn't look like an API key")
    try:
        encrypted = crypto.encrypt(api_key, api_key_context(user.id, spec.id))
    except crypto.EncryptionNotConfigured as exc:
        logger.error("DATA_ENCRYPTION_KEY is not configured, so API keys can't be saved")
        raise HTTPException(
            status_code=503, detail="Saving API keys isn't available because the server's encryption isn't configured"
        ) from exc

    row = _saved_keys(db, user).get(spec.id)
    if row is None:
        row = models.UserAPIKey(user_id=user.id, provider=spec.id)
        db.add(row)
    row.encrypted_key = encrypted
    row.last_four = api_key[-4:]
    row.updated_at = models.utcnow()
    row.last_verified_at = None
    db.commit()
    logger.info("User %s saved a %s API key", user.id, spec.id)
    return _key_out(row)


@router.delete("/me/api-keys/{provider}", status_code=status.HTTP_204_NO_CONTENT)
def delete_api_key(provider: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    spec = _provider(provider)
    row = _saved_keys(db, user).get(spec.id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"You haven't saved a {spec.label} key")
    db.delete(row)
    db.commit()
    logger.info("User %s deleted their %s API key", user.id, spec.id)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


@router.post("/me/api-keys/{provider}/test", dependencies=[Depends(ai_rate_limit)])
async def test_api_key(provider: str, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """Send a tiny request with the saved key.

    `valid` is null when the provider failed for a reason other than the key itself.
    """
    spec = _provider(provider)
    row = _saved_keys(db, user).get(spec.id)
    if row is None:
        raise HTTPException(status_code=404, detail=f"You haven't saved a {spec.label} key")
    # Chat models sort first, so a provider's key is checked with a chat model when it offers one.
    provider_models = [model for model in catalog_models(db, purpose=None) if model.provider == spec.id]
    if not provider_models:
        raise HTTPException(status_code=409, detail=f"No {spec.label} model is currently offered")
    model_id = provider_models[0].model_id
    api_key = decrypt_user_key(row)

    try:
        await asyncio.wait_for(
            adapters.call_provider(
                spec, model_id, "Reply with the single word: ready", api_key, json_schema=None, max_tokens=512
            ),
            KEY_TEST_TIMEOUT_SECONDS,
        )
    except Exception as exc:
        logger.info("Testing a saved %s key for user %s failed", spec.id, user.id, exc_info=True)
        code = adapters.status_code(exc)
        return {
            "valid": False if code in (401, 403) else None,
            "message": adapters.safe_error_message(spec, model_id, exc),
            "key": _key_out(row),
        }

    row.last_verified_at = models.utcnow()
    db.commit()
    return {"valid": True, "message": f"{spec.label} accepted the key.", "key": _key_out(row)}
