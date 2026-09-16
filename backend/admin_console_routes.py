"""The platform administration console: plans, subscriptions, organizations, users, system health, production
readiness, AI costs, and recent failures. The model catalog and benchmarks are in governance_routes.py."""

import logging
from datetime import datetime, timedelta
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

import entitlements
import models
import ops
from auth_routes import require_admin
from billing_routes import plan_out
from database import get_db
from entitlements import Account

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/admin", tags=["administration"], dependencies=[Depends(require_admin)])


# --- Plans ---


class PlanIn(BaseModel):
    code: str = Field(pattern=r"^[a-z][a-z0-9_-]{1,39}$")
    name: str = Field(min_length=1, max_length=100)
    description: str = Field("", max_length=2000)
    monthly_price_cents: int | None = Field(None, ge=0)
    yearly_price_cents: int | None = Field(None, ge=0)
    currency: str = Field("USD", pattern=r"^[A-Z]{3}$")
    limits: dict[str, Any] = Field(default_factory=dict)
    provider_prices: dict[str, dict[str, str]] = Field(default_factory=dict)
    public: bool = True
    active: bool = True
    position: int = 0


def _check_limits(limits: dict[str, Any]) -> dict[str, Any]:
    known = set(entitlements.LIMIT_LABELS) | set(entitlements.FEATURE_LABELS) | {"allowed_providers"}
    unknown = set(limits) - known
    if unknown:
        raise HTTPException(status_code=422, detail=f"Unknown limits: {', '.join(sorted(unknown))}")
    for name in entitlements.LIMIT_LABELS:
        value = limits.get(name)
        if value is not None and (not isinstance(value, int | float) or value < 0):
            raise HTTPException(status_code=422, detail=f"{name} must be a non-negative number, or null for unlimited")
    for name in entitlements.FEATURE_LABELS:
        if name in limits and not isinstance(limits[name], bool):
            raise HTTPException(status_code=422, detail=f"{name} must be true or false")
    return limits


def admin_plan_out(db: Session, plan: models.Plan) -> dict[str, Any]:
    subscribers = db.scalar(
        select(func.count())
        .select_from(models.Subscription)
        .where(
            models.Subscription.plan_id == plan.id, models.Subscription.status.in_(tuple(entitlements.ACTIVE_STATUSES))
        )
    )
    return {
        **plan_out(plan),
        "id": plan.id,
        "provider_prices": plan.provider_prices,
        "public": plan.public,
        "active": plan.active,
        "position": plan.position,
        "subscribers": subscribers or 0,
    }


@router.get("/plans")
def list_plans(db: Session = Depends(get_db)):
    plans = db.scalars(select(models.Plan).order_by(models.Plan.position, models.Plan.id))
    return [admin_plan_out(db, plan) for plan in plans]


@router.post("/plans", status_code=201)
def create_plan(body: PlanIn, db: Session = Depends(get_db)):
    plan = models.Plan(**{**body.model_dump(), "limits": _check_limits(body.limits)})
    db.add(plan)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A plan with that code already exists") from None
    return admin_plan_out(db, plan)


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, body: PlanIn, db: Session = Depends(get_db)):
    plan = db.get(models.Plan, plan_id)
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    if plan.code != body.code and plan.code == entitlements.free_plan_code():
        raise HTTPException(status_code=409, detail="The free plan's code can't change")
    for name, value in {**body.model_dump(), "limits": _check_limits(body.limits)}.items():
        setattr(plan, name, value)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="A plan with that code already exists") from None
    return admin_plan_out(db, plan)


# --- Subscriptions ---


class SubscriptionAssign(BaseModel):
    account_kind: Literal["user", "organization"]
    account_id: int
    plan_code: str = Field(min_length=1, max_length=40)
    status: Literal["active", "trialing", "past_due", "canceled"] = "active"
    current_period_end: datetime | None = None
    note: str = Field("", max_length=2000)


def subscription_out(db: Session, subscription: models.Subscription) -> dict[str, Any]:
    account = (
        Account("organization", subscription.organization_id)
        if subscription.organization_id is not None
        else Account("user", subscription.user_id or 0)
    )
    return {
        "id": subscription.id,
        "account_kind": account.kind,
        "account_id": account.id,
        "account": entitlements.account_label(db, account),
        "plan_code": subscription.plan.code,
        "plan": subscription.plan.name,
        "status": subscription.status,
        "interval": subscription.interval,
        "current_period_end": subscription.current_period_end,
        "cancel_at_period_end": subscription.cancel_at_period_end,
        "provider": subscription.provider,
        "note": subscription.note,
        "updated_at": subscription.updated_at,
    }


@router.get("/subscriptions")
def list_subscriptions(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Subscription).order_by(models.Subscription.updated_at.desc()))
    return [subscription_out(db, row) for row in rows]


@router.put("/subscriptions")
def assign_subscription(
    body: SubscriptionAssign, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)
):
    """Put an account on a plan by arrangement (for example an invoiced institution)."""
    account = Account(body.account_kind, body.account_id)
    exists = db.get(models.User if account.kind == "user" else models.Organization, account.id)
    if exists is None:
        raise HTTPException(status_code=404, detail="Account not found")
    plan = db.scalar(select(models.Plan).where(models.Plan.code == body.plan_code))
    if plan is None:
        raise HTTPException(status_code=404, detail="Plan not found")
    subscription = entitlements.subscription_for(db, account)
    if (
        subscription is not None
        and subscription.provider == "stripe"
        and subscription.status in entitlements.ACTIVE_STATUSES
    ):
        raise HTTPException(
            status_code=409,
            detail="This account pays through Stripe. Change or cancel the plan in Stripe so billing stays in step.",
        )
    if subscription is None:
        subscription = models.Subscription(
            user_id=account.id if account.kind == "user" else None,
            organization_id=account.id if account.kind == "organization" else None,
            plan_id=plan.id,
            status=body.status,
            provider="manual",
        )
        db.add(subscription)
    subscription.plan_id, subscription.status, subscription.provider = plan.id, body.status, "manual"
    subscription.interval, subscription.current_period_end = "none", body.current_period_end
    subscription.cancel_at_period_end = False
    subscription.provider_subscription_id = subscription.provider_customer_id = ""
    subscription.note = body.note
    db.commit()
    db.refresh(subscription)
    logger.info("Administrator %s put %s on the %s plan", admin.id, account.key, plan.code)
    return subscription_out(db, subscription)


# --- Organizations ---


class OrganizationIn(BaseModel):
    name: str = Field(min_length=2, max_length=200)


class OrganizationMemberIn(BaseModel):
    email: str = Field(min_length=3, max_length=254)
    role: Literal["owner", "admin", "member"] = "member"


def organization_out(db: Session, organization: models.Organization) -> dict[str, Any]:
    account = Account("organization", organization.id)
    plan = entitlements.plan_for(db, account)
    return {
        "id": organization.id,
        "name": organization.name,
        "created_at": organization.created_at,
        "plan": plan.name if plan else None,
        "projects": entitlements.usage_of(db, account, "projects"),
        "members": [
            {"user_id": m.user_id, "name": m.user.full_name, "email": m.user.email, "role": m.role}
            for m in organization.members
        ],
    }


@router.get("/organizations")
def list_organizations(db: Session = Depends(get_db)):
    rows = db.scalars(select(models.Organization).order_by(models.Organization.name))
    return [organization_out(db, row) for row in rows]


@router.post("/organizations", status_code=201)
def create_organization(body: OrganizationIn, db: Session = Depends(get_db)):
    organization = models.Organization(name=body.name.strip())
    db.add(organization)
    try:
        db.commit()
    except IntegrityError:
        db.rollback()
        raise HTTPException(status_code=409, detail="An organization with that name already exists") from None
    return organization_out(db, organization)


@router.put("/organizations/{organization_id}/members")
def set_organization_member(organization_id: int, body: OrganizationMemberIn, db: Session = Depends(get_db)):
    organization = db.get(models.Organization, organization_id)
    if organization is None:
        raise HTTPException(status_code=404, detail="Organization not found")
    user = db.scalar(select(models.User).where(func.lower(models.User.email) == body.email.strip().lower()))
    if user is None:
        raise HTTPException(status_code=404, detail="No account uses that email address")
    member = next((m for m in organization.members if m.user_id == user.id), None)
    if member is None:
        organization.members.append(models.OrganizationMember(user_id=user.id, role=body.role))
    else:
        member.role = body.role
    db.commit()
    db.refresh(organization)
    return organization_out(db, organization)


@router.delete("/organizations/{organization_id}/members/{user_id}", status_code=204)
def remove_organization_member(organization_id: int, user_id: int, db: Session = Depends(get_db)):
    member = db.scalar(
        select(models.OrganizationMember).where(
            models.OrganizationMember.organization_id == organization_id, models.OrganizationMember.user_id == user_id
        )
    )
    if member is None:
        raise HTTPException(status_code=404, detail="Member not found")
    db.delete(member)
    db.commit()
    return Response(status_code=204)


# --- Users ---


class UserUpdate(BaseModel):
    is_active: bool | None = None
    is_platform_admin: bool | None = None
    reset_mfa: bool = False


def user_out(db: Session, user: models.User) -> dict[str, Any]:
    plan = entitlements.plan_for(db, Account("user", user.id))
    return {
        "id": user.id,
        "name": user.full_name,
        "email": user.email,
        "institution": user.institution,
        "created_at": user.created_at,
        "is_active": user.is_active,
        "is_platform_admin": user.is_platform_admin,
        "email_verified": user.email_verified_at is not None,
        "mfa_enabled": user.mfa_enabled,
        "deletion_requested_at": user.deletion_requested_at,
        "deleted_at": user.deleted_at,
        "plan": plan.name if plan else None,
        "projects": entitlements.usage_of(db, Account("user", user.id), "projects"),
    }


@router.get("/users")
def list_users(
    q: str = Query("", max_length=200),
    limit: int = Query(50, ge=1, le=200),
    offset: int = Query(0, ge=0),
    db: Session = Depends(get_db),
):
    query = select(models.User)
    if q.strip():
        pattern = f"%{q.strip().lower()}%"
        query = query.where(
            or_(
                func.lower(models.User.email).like(pattern),
                func.lower(models.User.first_name + " " + models.User.last_name).like(pattern),
                func.lower(func.coalesce(models.User.institution, "")).like(pattern),
            )
        )
    total = db.scalar(select(func.count()).select_from(query.subquery())) or 0
    rows = db.scalars(query.order_by(models.User.id.desc()).offset(offset).limit(limit))
    return {"total": total, "users": [user_out(db, row) for row in rows]}


@router.patch("/users/{user_id}")
def update_user(
    user_id: int, body: UserUpdate, admin: models.User = Depends(require_admin), db: Session = Depends(get_db)
):
    user = db.get(models.User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User not found")
    if user.id == admin.id and (body.is_active is False or body.is_platform_admin is False):
        raise HTTPException(
            status_code=409, detail="You can't deactivate yourself or remove your own administrator rights"
        )
    if body.is_active is not None:
        user.is_active = body.is_active
    if body.is_platform_admin is not None:
        user.is_platform_admin = body.is_platform_admin
    if body.reset_mfa:
        user.mfa_enabled, user.mfa_secret_encrypted, user.mfa_last_step, user.mfa_recovery_hashes = (
            False,
            None,
            None,
            [],
        )
        logger.warning("Administrator %s reset two-factor sign-in for user %s", admin.id, user.id)
    db.commit()
    return user_out(db, user)


# --- Health, readiness, costs, failures ---


@router.get("/system")
def system(db: Session = Depends(get_db)):
    checks = ops.system_status(db)
    return {
        "status": "fail"
        if any(c.status == "fail" for c in checks)
        else "warn"
        if any(c.status == "warn" for c in checks)
        else "ok",
        "checks": [check.out() for check in checks],
    }


@router.get("/readiness")
def production_readiness(db: Session = Depends(get_db)):
    """The production readiness checklist (scripts/production_check.py): configuration and runtime checks."""
    from scripts.production_check import run_checks

    checks = run_checks(db)
    return {
        "ready": not any(check.status == "fail" for check in checks),
        "checks": [check.out() for check in checks],
    }


@router.get("/costs")
def costs(days: int = Query(30, ge=1, le=366), db: Session = Depends(get_db)):
    return {"days": days, **ops.spend(db, models.utcnow() - timedelta(days=days))}


@router.get("/failures")
def recent_failures(db: Session = Depends(get_db)):
    jobs = db.execute(
        select(models.AIJob, models.Project.title)
        .join(models.Project, models.Project.id == models.AIJob.project_id)
        .where(models.AIJob.status == "failed")
        .order_by(models.AIJob.id.desc())
        .limit(50)
    ).all()
    deliveries = db.scalars(
        select(models.WebhookDelivery)
        .where(models.WebhookDelivery.status == "failed")
        .order_by(models.WebhookDelivery.id.desc())
        .limit(50)
    ).all()
    events = db.scalars(
        select(models.BillingEvent)
        .where(models.BillingEvent.status == "failed")
        .order_by(models.BillingEvent.id.desc())
        .limit(50)
    ).all()
    return {
        "ai_jobs": [
            {"id": job.id, "project": title, "task": job.task, "error": job.error, "created_at": job.created_at}
            for job, title in jobs
        ],
        "webhook_deliveries": [
            {"id": d.id, "action": d.action, "error": d.error, "attempts": d.attempts, "created_at": d.created_at}
            for d in deliveries
        ],
        "billing_events": [
            {"id": e.id, "provider": e.provider, "type": e.type, "error": e.error, "received_at": e.received_at}
            for e in events
        ],
    }
