"""The catalog of AI models that projects can pin."""

from sqlalchemy import select
from sqlalchemy.orm import Session

import models
from llm.providers import PROVIDERS

_PROVIDER_ORDER = {provider_id: position for position, provider_id in enumerate(PROVIDERS)}


def default_model(db: Session, purpose: str = "chat") -> models.AIModel | None:
    return db.scalar(
        select(models.AIModel).where(
            models.AIModel.is_default, models.AIModel.enabled, models.AIModel.purpose == purpose
        )
    )


def catalog_models(db: Session, purpose: str | None = "chat") -> list[models.AIModel]:
    """Enabled models of known providers for `purpose` (None for all), grouped by provider, chat models first."""
    query = select(models.AIModel).where(models.AIModel.enabled)
    if purpose is not None:
        query = query.where(models.AIModel.purpose == purpose)
    known = [row for row in db.scalars(query) if row.provider in PROVIDERS]
    return sorted(
        known, key=lambda row: (_PROVIDER_ORDER[row.provider], row.purpose != "chat", not row.is_default, row.id)
    )


def model_ref(model: models.AIModel | None) -> str | None:
    return f"{model.provider}/{model.model_id}" if model else None


def ai_model_out(model: models.AIModel) -> dict:
    spec = PROVIDERS.get(model.provider)
    return {
        "id": model.id,
        "provider": model.provider,
        "provider_label": spec.label if spec else model.provider,
        "model_id": model.model_id,
        "label": model.label,
        "purpose": model.purpose,
        "is_default": model.is_default,
        "enabled": model.enabled,
        "data_location": spec.headquarters if spec else None,
    }
