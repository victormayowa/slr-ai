"""Billing for users: plans, an account's plan and usage, checkout, the payment provider's portal, cancellation, and
the provider's webhooks. Administrators assign plans in admin_console_routes.py."""

import logging
from decimal import Decimal
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Request
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import ai_credit
import billing
import entitlements
import models
from ai_catalog import ai_model_out, catalog_models
from auth_routes import get_current_user
from database import get_db
from entitlements import Account
from llm.providers import platform_keys_enabled

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/billing", tags=["billing"])


def plan_out(plan: models.Plan) -> dict:
    return {
        "code": plan.code,
        "name": plan.name,
        "description": plan.description,
        "monthly_price_cents": plan.monthly_price_cents,
        "yearly_price_cents": plan.yearly_price_cents,
        "currency": plan.currency,
        "limits": plan.limits,
        "online_intervals": sorted(((plan.provider_prices or {}).get("stripe") or {}).keys()),
    }


def config_out() -> dict:
    chosen_id = billing.provider_id()
    chosen = billing.PROVIDERS.get(chosen_id)
    return {
        "enabled": entitlements.billing_enabled(),
        "provider": chosen_id,
        "provider_label": chosen.label if chosen else chosen_id,
        "checkout_available": chosen is not None and chosen.configured() and chosen_id != "manual",
        "limit_labels": entitlements.LIMIT_LABELS,
        "feature_labels": entitlements.FEATURE_LABELS,
        # False when the server uses only users' own AI keys, so AI credits never apply.
        "platform_ai_keys": platform_keys_enabled(),
    }


@router.get("/plans")
def public_plans(db: Session = Depends(get_db)):
    """The plans shown on the pricing page. No sign-in needed."""
    plans = db.scalars(
        select(models.Plan)
        .where(models.Plan.public.is_(True), models.Plan.active.is_(True))
        .order_by(models.Plan.position)
    )
    return {"plans": [plan_out(plan) for plan in plans], **config_out()}


def _can_manage(db: Session, user: models.User, account: Account) -> bool:
    if user.is_platform_admin:
        return True
    if account.kind == "user":
        return account.id == user.id
    return (
        db.scalar(
            select(models.OrganizationMember.id).where(
                models.OrganizationMember.organization_id == account.id,
                models.OrganizationMember.user_id == user.id,
                models.OrganizationMember.role.in_(("owner", "admin")),
            )
        )
        is not None
    )


def _account(db: Session, user: models.User, kind: str, account_id: int) -> Account:
    if kind not in ("user", "organization"):
        raise HTTPException(status_code=404, detail="Account not found")
    account = Account(kind, account_id)  # type: ignore[arg-type]
    if not _can_manage(db, user, account):
        raise HTTPException(status_code=404, detail="Account not found")
    return account


def account_out(db: Session, account: Account) -> dict:
    subscription = entitlements.subscription_for(db, account)
    plan = entitlements.plan_for(db, account)
    return {
        "kind": account.kind,
        "id": account.id,
        "label": entitlements.account_label(db, account),
        "plan": plan_out(plan) if plan else None,
        "subscription": (
            {
                "plan_code": subscription.plan.code,
                "status": subscription.status,
                "interval": subscription.interval,
                "current_period_end": subscription.current_period_end,
                "cancel_at_period_end": subscription.cancel_at_period_end,
                "provider": subscription.provider,
            }
            if subscription
            else None
        ),
        "usage": entitlements.usage(db, account),
        "usage_resets_on": entitlements.next_month_start(),
        "ai_balance_usd": ai_credit.balance(db, account),
        "ai_entries": [
            {
                "id": entry.id,
                "kind": entry.kind,
                "amount_usd": entry.amount_usd,
                "description": entry.description,
                "created_at": entry.created_at,
            }
            for entry in ai_credit.entries(db, account, limit=20)
        ],
        **config_out(),
    }


@router.get("/accounts")
def list_accounts(user: models.User = Depends(get_current_user), db: Session = Depends(get_db)):
    """The billing accounts you manage: your own, and organizations where you are an owner or admin."""
    accounts = [Account("user", user.id)]
    for membership in user.organization_memberships:
        if membership.role in ("owner", "admin"):
            accounts.append(Account("organization", membership.organization_id))
    return [
        {
            "kind": account.kind,
            "id": account.id,
            "label": entitlements.account_label(db, account),
            "plan": (plan.name if (plan := entitlements.plan_for(db, account)) else None),
        }
        for account in accounts
    ]


@router.get("/accounts/{kind}/{account_id}")
def get_account(
    kind: str, account_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    return account_out(db, _account(db, user, kind, account_id))


class CheckoutIn(BaseModel):
    plan_code: str = Field(min_length=1, max_length=40)
    interval: Literal["month", "year"] = "month"


@router.post("/accounts/{kind}/{account_id}/checkout")
def start_checkout(
    kind: str,
    account_id: int,
    body: CheckoutIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A link to the payment provider's checkout for the plan. An account already paying online changes plan in the
    provider's portal instead, so it is never charged for two subscriptions."""
    account = _account(db, user, kind, account_id)
    plan = db.scalar(select(models.Plan).where(models.Plan.code == body.plan_code, models.Plan.active.is_(True)))
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.monthly_price_cents is None and plan.yearly_price_cents is None:
        raise HTTPException(
            status_code=409, detail=f"The {plan.name} plan is arranged with us directly. Contact sales."
        )
    if plan.code == entitlements.free_plan_code():
        raise HTTPException(status_code=409, detail="Cancel the current subscription to return to the free plan")
    try:
        chosen = billing.provider()
        existing = entitlements.subscription_for(db, account)
        if (
            existing is not None
            and existing.provider == chosen.id
            and existing.provider_subscription_id
            and existing.status in entitlements.ACTIVE_STATUSES
            and chosen.id == "stripe"
        ):
            return {"url": chosen.portal(existing, billing.app_url("/billing")), "kind": "portal"}
        request = billing.CheckoutRequest(
            account=account,
            plan=plan,
            interval=body.interval,
            email=billing.account_email(db, account, user),
            success_url=billing.app_url("/billing?checkout=success"),
            cancel_url=billing.app_url("/billing?checkout=cancelled"),
            existing=existing,
        )
        return {"url": chosen.checkout(request), "kind": "checkout"}
    except billing.BillingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.get("/ai-prices")
def ai_prices(db: Session = Depends(get_db)):
    """What each AI engine costs on the server's keys. No sign-in needed: it belongs on the pricing page.

    Work on your own API key is never charged here; the provider bills you directly.
    """
    engines = []
    for model in catalog_models(db, purpose=None):
        prices = ai_credit.model_prices(model)
        if prices["input_per_mtok"] is None:
            continue
        engines.append(
            {
                "provider": model.provider,
                "provider_label": ai_model_out(model)["provider_label"],
                "model": model.model_id,
                "label": model.label,
                "purpose": model.purpose,
                "data_location": ai_model_out(model)["data_location"],
                **prices,
            }
        )
    return {
        "engines": engines,
        "currency": "USD",
        "unit": "per million tokens",
        "own_keys_free": True,
        "platform_ai_keys": platform_keys_enabled(),
    }


class TopUpIn(BaseModel):
    # Whole dollars, enough to matter and not so much that a mistyped amount is painful.
    amount_usd: int = Field(ge=5, le=5000)


@router.post("/accounts/{kind}/{account_id}/ai-credit/checkout")
def start_ai_topup(
    kind: str,
    account_id: int,
    body: TopUpIn,
    user: models.User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """A link to pay for AI credit. The balance moves when the payment is confirmed, not when the link is made."""
    account = _account(db, user, kind, account_id)
    try:
        chosen = billing.provider()
        return {
            "url": chosen.topup(
                billing.TopUpRequest(
                    account=account,
                    amount_usd=Decimal(body.amount_usd),
                    email=billing.account_email(db, account, user),
                    success_url=billing.app_url("/billing?topup=success"),
                    cancel_url=billing.app_url("/billing?topup=cancelled"),
                )
            )
        }
    except billing.BillingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/accounts/{kind}/{account_id}/portal")
def open_portal(
    kind: str, account_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    account = _account(db, user, kind, account_id)
    subscription = entitlements.subscription_for(db, account)
    if subscription is None:
        raise HTTPException(status_code=409, detail="This account has no subscription to manage")
    try:
        chosen = billing.PROVIDERS.get(subscription.provider) or billing.provider()
        return {"url": chosen.portal(subscription, billing.app_url("/billing"))}
    except billing.BillingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc


@router.post("/accounts/{kind}/{account_id}/cancel")
def cancel_subscription(
    kind: str, account_id: int, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Cancel at the end of the paid period. Plans assigned by an administrator are cancelled by an administrator."""
    account = _account(db, user, kind, account_id)
    subscription = entitlements.subscription_for(db, account)
    if subscription is None or subscription.status == "canceled":
        raise HTTPException(status_code=409, detail="There is no active subscription to cancel")
    if subscription.provider == "manual":
        raise HTTPException(
            status_code=409, detail="This plan was arranged with us directly. Contact support to end it."
        )
    try:
        chosen = billing.PROVIDERS[subscription.provider]
        chosen.cancel(subscription, at_period_end=True)
    except (billing.BillingError, KeyError) as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    subscription.cancel_at_period_end = True
    if subscription.provider == "dev" or subscription.current_period_end is None:
        # Development payments have no provider to end the period, so the plan ends now.
        subscription.status = "canceled"
    db.commit()
    return account_out(db, account)


@router.post("/webhooks/{provider_name}")
async def receive_webhook(provider_name: str, request: Request, db: Session = Depends(get_db)):
    """Payment provider webhooks. Verified by signature; each event is applied once."""
    chosen = billing.PROVIDERS.get(provider_name)
    if chosen is None or not chosen.configured() or provider_name != billing.provider_id():
        raise HTTPException(status_code=404, detail="Unknown billing provider")
    body = await request.body()
    try:
        result = billing.handle_webhook(db, chosen, body, dict(request.headers))
    except billing.BillingError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return {"result": result}


class DevCompletion(BaseModel):
    session: str = Field(min_length=10, max_length=2000)
    outcome: Literal["paid", "failed"] = "paid"


@router.post("/dev/complete")
def complete_dev_checkout(
    body: DevCompletion, user: models.User = Depends(get_current_user), db: Session = Depends(get_db)
):
    """Development only: finish a simulated checkout by sending its signed webhook through the real webhook path."""
    dev = billing.PROVIDERS["dev"]
    if billing.provider_id() != "dev" or not dev.configured() or not isinstance(dev, billing.DevProvider):
        raise HTTPException(status_code=404, detail="Simulated payments aren't enabled")
    try:
        session = dev.read_session(body.session)
        account = Account.parse(session["account"])
        if account is None or not _can_manage(db, user, account):
            raise HTTPException(status_code=404, detail="Account not found")
        payload, headers = dev.completion_webhook(body.session, body.outcome)
        result = billing.handle_webhook(db, dev, payload, headers)
    except billing.BillingError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"result": result, "account": account_out(db, account)}
