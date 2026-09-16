"""Webhooks: audit events matching a subscription's patterns are queued as deliveries in the same transaction, and the
worker POSTs them with an HMAC-SHA256 signature, retrying failures with increasing delays."""

import fnmatch
import hashlib
import hmac
import json
import logging
from datetime import datetime, timedelta
from typing import Any

import requests
from sqlalchemy import select
from sqlalchemy.orm import Session

import crypto
import models
from net_safety import UnsafeURL, ensure_public_url

logger = logging.getLogger(__name__)

MAX_ATTEMPTS = 6
BACKOFF_SECONDS = (60, 300, 900, 3600, 21600)
DISABLE_AFTER_FAILURES = 20
TIMEOUT_SECONDS = 10


def secret_context(project_id: int) -> str:
    return f"webhook:{project_id}"


def matches(patterns: list[str], action: str) -> bool:
    return any(fnmatch.fnmatchcase(action, pattern) for pattern in patterns)


def event_payload(event: models.AuditEvent) -> dict[str, Any]:
    return {
        "id": event.id,
        "project_id": event.project_id,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "actor_id": event.actor_id,
        "details": event.details,
        "created_at": event.created_at.isoformat(),
        "hash": event.hash,
    }


def queue_deliveries(db: Session, event: models.AuditEvent, only: models.WebhookSubscription | None = None) -> int:
    subscriptions = (
        [only]
        if only is not None
        else db.scalars(
            select(models.WebhookSubscription).where(
                models.WebhookSubscription.project_id == event.project_id, models.WebhookSubscription.active.is_(True)
            )
        ).all()
    )
    queued = 0
    for subscription in subscriptions:
        if only is None and not matches(subscription.events, event.action):
            continue
        db.add(
            models.WebhookDelivery(
                subscription_id=subscription.id,
                audit_event_id=event.id,
                action=event.action,
                payload=event_payload(event),
                status="pending",
                next_attempt_at=models.utcnow(),
            )
        )
        queued += 1
    return queued


def signature(secret: str, body: bytes) -> str:
    return "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


def deliver(db: Session, delivery: models.WebhookDelivery, now: datetime) -> bool:
    subscription = delivery.subscription
    body = json.dumps(delivery.payload, sort_keys=True, default=str).encode()
    delivery.attempts += 1
    try:
        ensure_public_url(subscription.url)
        secret = crypto.decrypt(subscription.secret_encrypted, secret_context(subscription.project_id))
        response = requests.post(
            subscription.url,
            data=body,
            timeout=TIMEOUT_SECONDS,
            allow_redirects=False,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "OmniReview-Webhooks/1.0",
                "X-OmniReview-Event": delivery.action,
                "X-OmniReview-Delivery": str(delivery.id),
                "X-OmniReview-Signature": signature(secret, body),
            },
        )
        delivery.response_status = response.status_code
        ok = 200 <= response.status_code < 300
        delivery.error = None if ok else f"HTTP {response.status_code}"
    except (requests.RequestException, UnsafeURL) as exc:
        ok = False
        delivery.error = str(exc) if isinstance(exc, UnsafeURL) else f"Request failed: {type(exc).__name__}"
    if ok:
        delivery.status, delivery.delivered_at = "delivered", now
        subscription.failure_count, subscription.last_delivery_at = 0, now
        return True
    if delivery.attempts >= MAX_ATTEMPTS:
        delivery.status = "failed"
        subscription.failure_count += 1
        if subscription.failure_count >= DISABLE_AFTER_FAILURES:
            subscription.active = False
    else:
        delivery.next_attempt_at = now + timedelta(seconds=BACKOFF_SECONDS[delivery.attempts - 1])
    return False


def deliver_due(db: Session, now: datetime | None = None, limit: int = 100) -> int:
    """Attempt every due delivery. Returns how many were delivered."""
    now = now or models.utcnow()
    due = db.scalars(
        select(models.WebhookDelivery)
        .join(models.WebhookSubscription)
        .where(
            models.WebhookDelivery.status == "pending",
            models.WebhookDelivery.next_attempt_at <= now,
            models.WebhookSubscription.active.is_(True),
        )
        .order_by(models.WebhookDelivery.id)
        .limit(limit)
    ).all()
    delivered = sum(1 for delivery in due if deliver(db, delivery, now))
    db.commit()
    return delivered
