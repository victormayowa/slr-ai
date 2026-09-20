"""The prepaid balance that pays for AI run on the server's keys, and what that AI costs.

Work on a user's own API key is never charged here: the provider bills them directly. Work on the server's keys is
charged to the project's billing account at the published price, which is what the provider charges multiplied by
AI_PRICE_MARKUP (four by default). The prices are shown per engine on the pricing page, so a reviewer can see what
each model costs before choosing it.

The balance is the sum of the account's ledger entries (models.AICreditEntry), never a stored total, so it can't drift
from the entries that explain it. Every AI run is charged exactly once: the charge carries the run's reference, and
the unique index refuses a second entry for the same run.

Charging only happens when billing is enabled, so self-hosted and development servers are unaffected.
"""

import logging
import os
from decimal import Decimal, InvalidOperation

from fastapi import HTTPException
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import models
from entitlements import Account, account_for_project, billing_enabled

logger = logging.getLogger(__name__)

DEFAULT_MARKUP = Decimal("4")
_CENT = Decimal("0.01")
_MICRO = Decimal("0.000001")


def markup() -> Decimal:
    """What the server charges for AI, as a multiple of what the provider charges."""
    setting = os.getenv("AI_PRICE_MARKUP", "").strip()
    if not setting:
        return DEFAULT_MARKUP
    try:
        value = Decimal(setting)
    except InvalidOperation:
        logger.warning("AI_PRICE_MARKUP isn't a number; using %s", DEFAULT_MARKUP)
        return DEFAULT_MARKUP
    return value if value > 0 else DEFAULT_MARKUP


def price_per_mtok(cost_per_mtok: Decimal | None) -> Decimal | None:
    """What a million tokens costs a customer, from what it costs the server. None when the cost isn't recorded."""
    if cost_per_mtok is None:
        return None
    return (Decimal(cost_per_mtok) * markup()).quantize(_CENT)


def model_prices(model: models.AIModel) -> dict[str, Decimal | None]:
    return {
        "input_per_mtok": price_per_mtok(model.input_price_per_mtok),
        "output_per_mtok": price_per_mtok(model.output_price_per_mtok),
    }


def priced(model: models.AIModel) -> bool:
    """Whether this model can be charged for. Models without prices can only be used with a user's own key."""
    return model.input_price_per_mtok is not None and model.output_price_per_mtok is not None


def _column(account: Account):
    return models.AICreditEntry.user_id if account.kind == "user" else models.AICreditEntry.organization_id


def balance(db: Session, account: Account) -> Decimal:
    total = db.scalar(
        select(func.coalesce(func.sum(models.AICreditEntry.amount_usd), 0)).where(_column(account) == account.id)
    )
    return Decimal(total or 0).quantize(_MICRO)


def entries(db: Session, account: Account, limit: int = 50) -> list[models.AICreditEntry]:
    return list(
        db.scalars(
            select(models.AICreditEntry)
            .where(_column(account) == account.id)
            .order_by(models.AICreditEntry.id.desc())
            .limit(limit)
        )
    )


def add_entry(
    db: Session,
    account: Account,
    *,
    kind: str,
    amount_usd: Decimal,
    description: str = "",
    reference: str | None = None,
    ai_run_id: int | None = None,
    created_by_id: int | None = None,
) -> models.AICreditEntry | None:
    """Add one movement to the account's balance. Returns None when `reference` has already been recorded, so a
    repeated webhook or a rerun job neither charges nor credits twice."""
    entry = models.AICreditEntry(
        user_id=account.id if account.kind == "user" else None,
        organization_id=account.id if account.kind == "organization" else None,
        kind=kind,
        amount_usd=amount_usd.quantize(_MICRO),
        description=description[:300],
        reference=reference,
        ai_run_id=ai_run_id,
        created_by_id=created_by_id,
    )
    try:
        # A savepoint, so a duplicate reference costs this entry and not the transaction around it.
        with db.begin_nested():
            db.add(entry)
            db.flush()
    except IntegrityError:
        logger.info("AI credit entry %s was already recorded", reference)
        return None
    return entry


def run_charge(run: models.AIRun) -> Decimal | None:
    """What an AI run costs its account: what the provider charged, multiplied by the markup."""
    if run.key_source != "platform" or run.cost_usd is None:
        return None
    return (Decimal(run.cost_usd) * markup()).quantize(_MICRO)


def charge_run(db: Session, project: models.Project, run: models.AIRun) -> Decimal | None:
    """Charge one finished run to the project's billing account. Charging again does nothing: the entry carries the
    run's reference, and the unique index refuses a second one."""
    amount = run_charge(run)
    if amount is None or amount <= 0:
        return None
    entry = add_entry(
        db,
        account_for_project(project),
        kind="usage",
        amount_usd=-amount,
        description=f"{run.provider} {run.model} ({run.task})",
        reference=f"run:{run.id}",
        ai_run_id=run.id,
    )
    return amount if entry is not None else None


def charge_unbilled_runs(db: Session, limit: int = 500) -> int:
    """Charge every run on the server's keys that hasn't been charged yet, and return how many.

    Runs finish in many places - routes, background jobs, surveillance - so rather than charging at each of them, the
    charge is made from what the runs record. The worker sweeps every minute, and the balance check sweeps before it
    decides, so a balance is never stale when it matters.
    """
    if not billing_enabled():
        return 0
    uncharged = db.scalars(
        select(models.AIRun)
        .outerjoin(models.AICreditEntry, models.AICreditEntry.ai_run_id == models.AIRun.id)
        .where(
            models.AIRun.key_source == "platform",
            models.AIRun.cost_usd.is_not(None),
            models.AICreditEntry.id.is_(None),
        )
        .order_by(models.AIRun.id)
        .limit(limit)
    ).all()
    count = 0
    for run in uncharged:
        project = db.get(models.Project, run.project_id)
        if project is None:
            continue
        if charge_run(db, project, run) is not None:
            count += 1
    if count:
        logger.info("Charged %s AI run(s) to prepaid balances", count)
    return count


def require_balance(db: Session, project: models.Project, model: models.AIModel) -> None:
    """Refuse AI on the server's keys when nobody has priced the model yet, or when the account has nothing left to
    pay with. Work on a user's own key never reaches this."""
    if not billing_enabled():
        return
    # Charge what has run since the last sweep, so the balance below is current.
    charge_unbilled_runs(db)
    if not priced(model):
        raise HTTPException(
            status_code=409,
            detail=(
                f'"{model.label}" has no price on this server yet, so it can only be used with your own API key. '
                "Add your key in Settings, or choose another model in Project Setup."
            ),
        )
    if balance(db, account_for_project(project)) <= 0:
        raise HTTPException(
            status_code=402,
            detail=(
                "Your AI balance is empty. Top it up under Billing to keep using the AI included with OmniReview, "
                "or add your own provider key in Settings, which is never charged."
            ),
        )
