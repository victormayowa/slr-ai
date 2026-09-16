"""Webhook subscriptions for a project. The signing secret is shown once when the subscription is created."""

import secrets

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

import crypto
import models
import webhooks
from audit import record_event
from database import get_db
from net_safety import UnsafeURL, ensure_public_url
from permissions import Permission
from projects_routes import ProjectAccess, get_in_project, project_access

router = APIRouter(prefix="/api/projects/{project_id}", tags=["webhooks"])
MAX_SUBSCRIPTIONS = 10


class SubscriptionIn(BaseModel):
    url: str = Field(min_length=8, max_length=1000)
    events: list[str] = Field(min_length=1, max_length=40)
    active: bool = True


def subscription_out(subscription: models.WebhookSubscription) -> dict:
    return {
        "id": subscription.id,
        "url": subscription.url,
        "events": subscription.events,
        "active": subscription.active,
        "failure_count": subscription.failure_count,
        "last_delivery_at": subscription.last_delivery_at,
        "created_at": subscription.created_at,
    }


def delivery_out(delivery: models.WebhookDelivery) -> dict:
    return {
        "id": delivery.id,
        "subscription_id": delivery.subscription_id,
        "action": delivery.action,
        "status": delivery.status,
        "attempts": delivery.attempts,
        "response_status": delivery.response_status,
        "error": delivery.error,
        "next_attempt_at": delivery.next_attempt_at,
        "delivered_at": delivery.delivered_at,
        "created_at": delivery.created_at,
    }


def _check_url(url: str) -> str:
    url = url.strip()
    try:
        ensure_public_url(url)
    except UnsafeURL as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return url


def _check_events(events: list[str]) -> list[str]:
    cleaned = sorted({event.strip() for event in events if event.strip()})
    if not cleaned:
        raise HTTPException(status_code=422, detail="Choose at least one event pattern, for example stage.*")
    for event in cleaned:
        if len(event) > 60 or not all(character.isalnum() or character in "._-*?" for character in event):
            raise HTTPException(status_code=422, detail=f"{event} isn't a valid event pattern")
    return cleaned


@router.get("/webhooks")
def list_webhooks(
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)), db: Session = Depends(get_db)
):
    rows = db.scalars(
        select(models.WebhookSubscription)
        .where(models.WebhookSubscription.project_id == access.project.id)
        .order_by(models.WebhookSubscription.id)
    )
    return [subscription_out(row) for row in rows]


@router.post("/webhooks", status_code=201)
def create_webhook(
    body: SubscriptionIn,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Create a subscription. The secret is returned once; store it to verify X-OmniReview-Signature."""
    existing = db.scalars(
        select(models.WebhookSubscription).where(models.WebhookSubscription.project_id == access.project.id)
    ).all()
    if len(existing) >= MAX_SUBSCRIPTIONS:
        raise HTTPException(status_code=409, detail=f"A project can have at most {MAX_SUBSCRIPTIONS} webhooks")
    secret = secrets.token_urlsafe(32)
    try:
        encrypted = crypto.encrypt(secret, webhooks.secret_context(access.project.id))
    except crypto.EncryptionNotConfigured as exc:
        raise HTTPException(status_code=503, detail="Webhooks need the server's encryption to be configured") from exc
    subscription = models.WebhookSubscription(
        project_id=access.project.id,
        url=_check_url(body.url),
        secret_encrypted=encrypted,
        events=_check_events(body.events),
        active=body.active,
        created_by_id=access.user.id,
    )
    db.add(subscription)
    db.flush()
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="webhook.created",
        entity_type="webhook",
        entity_id=subscription.id,
        details={"url": subscription.url, "events": subscription.events},
    )
    db.commit()
    return {**subscription_out(subscription), "secret": secret}


@router.patch("/webhooks/{webhook_id}")
def update_webhook(
    webhook_id: int,
    body: SubscriptionIn,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    subscription = get_in_project(db, models.WebhookSubscription, webhook_id, access.project.id, "Webhook")
    subscription.url = _check_url(body.url)
    subscription.events = _check_events(body.events)
    subscription.active = body.active
    if body.active:
        subscription.failure_count = 0
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="webhook.updated",
        entity_type="webhook",
        entity_id=subscription.id,
        details={"url": subscription.url, "events": subscription.events, "active": subscription.active},
    )
    db.commit()
    return subscription_out(subscription)


@router.delete("/webhooks/{webhook_id}", status_code=204)
def delete_webhook(
    webhook_id: int,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    subscription = get_in_project(db, models.WebhookSubscription, webhook_id, access.project.id, "Webhook")
    record_event(
        db,
        project_id=access.project.id,
        actor_id=access.user.id,
        action="webhook.deleted",
        entity_type="webhook",
        entity_id=subscription.id,
        details={"url": subscription.url},
    )
    db.delete(subscription)
    db.commit()
    return Response(status_code=204)


@router.get("/webhooks/{webhook_id}/deliveries")
def list_deliveries(
    webhook_id: int,
    limit: int = 50,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    subscription = get_in_project(db, models.WebhookSubscription, webhook_id, access.project.id, "Webhook")
    rows = db.scalars(
        select(models.WebhookDelivery)
        .where(models.WebhookDelivery.subscription_id == subscription.id)
        .order_by(models.WebhookDelivery.id.desc())
        .limit(max(1, min(limit, 200)))
    )
    return [delivery_out(row) for row in rows]


@router.post("/webhooks/{webhook_id}/test", status_code=202)
def test_webhook(
    webhook_id: int,
    access: ProjectAccess = Depends(project_access(Permission.MANAGE_WORKFLOW)),
    db: Session = Depends(get_db),
):
    """Queue a delivery of the project's most recent audit event, whatever the subscription's patterns."""
    subscription = get_in_project(db, models.WebhookSubscription, webhook_id, access.project.id, "Webhook")
    event = db.scalar(
        select(models.AuditEvent)
        .where(models.AuditEvent.project_id == access.project.id)
        .order_by(models.AuditEvent.id.desc())
        .limit(1)
    )
    if event is None:
        raise HTTPException(status_code=409, detail="This project has no audit events to send yet")
    webhooks.queue_deliveries(db, event, only=subscription)
    db.commit()
    return {"queued": 1, "action": event.action}
