"""Plan limits: what a billing account (a user, or an organization) may use, and how much it has used.

A project belongs to its organization's account when it has one, otherwise to its owner's personal account. Usage is
measured from what the database already records, rather than from a separate counter that could drift:

- projects: the account's projects;
- members_per_project: the members of the project being changed;
- records_per_month: records added to the account's projects since the start of the month (searches and imports);
- storage_mb: stored document files kept in OmniReview's storage (files in the account's own bucket don't count);
- living_schedules: active surveillance schedules;
- compute_minutes_per_month: time spent running analyses in R this month.

Features (api_access, webhooks, bring_your_own_storage) are on or off per plan. Limits are enforced only when
BILLING_ENABLED=true, so self-hosted and development installations are unlimited by default.
A limit of null means unlimited.
"""

import os
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Literal

from fastapi import HTTPException
from sqlalchemy import and_, func, select
from sqlalchemy.orm import Session

import models

LIMIT_LABELS: dict[str, str] = {
    "projects": "projects",
    "members_per_project": "members in a project",
    "records_per_month": "records added this month",
    "storage_mb": "MB of stored documents",
    "living_schedules": "active surveillance schedules",
    "compute_minutes_per_month": "minutes of analysis compute this month",
}
FEATURE_LABELS: dict[str, str] = {
    "api_access": "API access with personal tokens",
    "webhooks": "webhooks",
    "bring_your_own_storage": "storing files in your own bucket",
}
# Usage measured over the calendar month rather than as a total.
MONTHLY = {"records_per_month", "compute_minutes_per_month"}
# Consumed as work runs, so the check is whether any allowance is left rather than whether an addition fits.
METERED = {"compute_minutes_per_month"}
ACTIVE_STATUSES = {"active", "trialing", "past_due"}
_MB = 1024 * 1024


@dataclass(frozen=True)
class Account:
    kind: Literal["user", "organization"]
    id: int

    @property
    def key(self) -> str:
        return f"{self.kind}:{self.id}"

    @classmethod
    def parse(cls, key: str) -> "Account | None":
        kind, _, raw_id = key.partition(":")
        if kind not in ("user", "organization") or not raw_id.isdigit():
            return None
        return cls(kind, int(raw_id))  # type: ignore[arg-type]


class LimitReached(HTTPException):
    """The account's plan doesn't allow this. Answered with 402 Payment Required and a message naming the limit."""

    def __init__(self, detail: str) -> None:
        super().__init__(status_code=402, detail=detail)


def billing_enabled() -> bool:
    return os.getenv("BILLING_ENABLED", "false").strip().lower() == "true"


def free_plan_code() -> str:
    return os.getenv("BILLING_FREE_PLAN", "free")


def account_for_user(user: models.User) -> Account:
    return Account("user", user.id)


def account_for_project(project: models.Project) -> Account:
    if project.organization_id is not None:
        return Account("organization", project.organization_id)
    if project.owner_id is not None:
        return Account("user", project.owner_id)
    owner = next((member for member in project.members if member.role == "owner"), None)
    return Account("user", owner.user_id if owner else 0)


def account_label(db: Session, account: Account) -> str:
    if account.kind == "organization":
        organization = db.get(models.Organization, account.id)
        return organization.name if organization else f"Organization {account.id}"
    user = db.get(models.User, account.id)
    return user.full_name if user else f"User {account.id}"


def subscription_for(db: Session, account: Account) -> models.Subscription | None:
    column = models.Subscription.user_id if account.kind == "user" else models.Subscription.organization_id
    return db.scalar(select(models.Subscription).where(column == account.id))


def plan_for(db: Session, account: Account) -> models.Plan | None:
    """The plan in force: an active subscription's plan, otherwise the free plan (None when it doesn't exist)."""
    subscription = subscription_for(db, account)
    if subscription is not None and subscription.status in ACTIVE_STATUSES:
        return subscription.plan
    return db.scalar(select(models.Plan).where(models.Plan.code == free_plan_code()))


def month_start(now: datetime | None = None) -> datetime:
    now = now or models.utcnow()
    return datetime(now.year, now.month, 1, tzinfo=UTC)


def next_month_start(now: datetime | None = None) -> datetime:
    start = month_start(now)
    return datetime(start.year + (start.month == 12), start.month % 12 + 1, 1, tzinfo=UTC)


def _projects_condition(account: Account):
    if account.kind == "organization":
        return models.Project.organization_id == account.id
    return and_(models.Project.organization_id.is_(None), models.Project.owner_id == account.id)


def project_ids(account: Account):
    return select(models.Project.id).where(_projects_condition(account)).scalar_subquery()


def usage_of(db: Session, account: Account, limit: str) -> float:
    since = month_start()
    if limit == "projects":
        return db.scalar(select(func.count()).select_from(models.Project).where(_projects_condition(account))) or 0
    if limit == "members_per_project":
        counts = db.execute(
            select(func.count())
            .select_from(models.ProjectMember)
            .where(models.ProjectMember.project_id.in_(project_ids(account)))
            .group_by(models.ProjectMember.project_id)
        ).scalars()
        return max(counts, default=0)
    if limit == "records_per_month":
        return (
            db.scalar(
                select(func.count())
                .select_from(models.Record)
                .where(models.Record.project_id.in_(project_ids(account)), models.Record.created_at >= since)
            )
            or 0
        )
    if limit == "storage_mb":
        size = db.scalar(
            select(func.coalesce(func.sum(models.Document.size_bytes), 0)).where(
                models.Document.project_id.in_(project_ids(account)),
                models.Document.storage_key.not_like("s3:%"),
            )
        )
        return round(float(size or 0) / _MB, 1)
    if limit == "living_schedules":
        return (
            db.scalar(
                select(func.count())
                .select_from(models.SurveillanceSchedule)
                .where(
                    models.SurveillanceSchedule.project_id.in_(project_ids(account)),
                    models.SurveillanceSchedule.active.is_(True),
                )
            )
            or 0
        )
    if limit == "compute_minutes_per_month":
        milliseconds = db.scalar(
            select(func.coalesce(func.sum(models.AnalysisRun.duration_ms), 0)).where(
                models.AnalysisRun.project_id.in_(project_ids(account)), models.AnalysisRun.created_at >= since
            )
        )
        return round(float(milliseconds or 0) / 60000, 1)
    raise ValueError(f"Unknown limit: {limit}")


def usage(db: Session, account: Account) -> dict[str, float]:
    return {limit: usage_of(db, account, limit) for limit in LIMIT_LABELS}


def _number(value: float) -> str:
    return f"{value:,.0f}" if float(value).is_integer() else f"{value:,.1f}"


def require(db: Session, account: Account, limit: str, adding: float = 1, current: float | None = None) -> None:
    """Raise LimitReached unless the account's plan allows `adding` more of `limit` (or, for metered usage, has any
    allowance left this month)."""
    if not billing_enabled():
        return
    plan = plan_for(db, account)
    if plan is None:
        return
    maximum = plan.limits.get(limit)
    if maximum is None:
        return
    used = current if current is not None else usage_of(db, account, limit)
    label = LIMIT_LABELS[limit]
    if limit in METERED:
        if used >= maximum:
            reset = next_month_start().strftime("%-d %B")
            raise LimitReached(
                f"The {plan.name} plan's {_number(maximum)} {label} are used up. They reset on {reset}; upgrade "
                "under Billing to continue now."
            )
        return
    if used + adding > maximum:
        raise LimitReached(
            f"The {plan.name} plan allows {_number(maximum)} {label}, and this would make {_number(used + adding)}. "
            "Upgrade under Billing to continue."
        )


def require_members(db: Session, project: models.Project, adding: int = 1) -> None:
    require(db, account_for_project(project), "members_per_project", adding, current=len(project.members))


def feature_allowed(db: Session, account: Account, feature: str) -> bool:
    if not billing_enabled():
        return True
    plan = plan_for(db, account)
    return plan is None or bool(plan.limits.get(feature, False))


def require_feature(db: Session, account: Account, feature: str) -> None:
    if feature_allowed(db, account, feature):
        return
    plan = plan_for(db, account)
    name = plan.name if plan else "current"
    raise LimitReached(f"The {name} plan doesn't include {FEATURE_LABELS[feature]}. Upgrade under Billing.")


def require_user_feature(db: Session, user: models.User, feature: str) -> None:
    """For features that act as a person across all their projects (API tokens): allowed when the personal plan, or
    the plan of any organization the person belongs to, includes the feature."""
    if not billing_enabled():
        return
    accounts = [account_for_user(user)] + [
        Account("organization", membership.organization_id) for membership in user.organization_memberships
    ]
    if any((plan := plan_for(db, account)) is None or plan.limits.get(feature, False) for account in accounts):
        return
    personal = plan_for(db, accounts[0])
    raise LimitReached(
        f"The {personal.name if personal else 'current'} plan doesn't include {FEATURE_LABELS[feature]}, and neither "
        "does any organization you belong to. Upgrade under Billing."
    )


def require_ai(db: Session, project: models.Project, provider: str) -> None:
    """Checked before AI work that uses the server's API keys: which providers the plan allows. What the work costs is
    taken from the account's prepaid balance (ai_credit.py). Work with a user's own key is never limited."""
    account = account_for_project(project)
    if not billing_enabled():
        return
    plan = plan_for(db, account)
    allowed = plan.limits.get("allowed_providers") if plan else None
    if allowed and provider not in allowed:
        raise LimitReached(
            f"The {plan.name if plan else 'current'} plan doesn't include this AI provider on the server's keys. "
            "Choose another model, add your own API key in Settings, or upgrade under Billing."
        )
