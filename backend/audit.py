"""Append-only project audit trail. Each event's hash covers its content and the previous event's hash, so any
edit to or removal of an earlier event breaks the chain and is detected by verify_chain."""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

import models

GENESIS_HASH = "0" * 64


def _canonical_time(moment: datetime) -> str:
    # SQLite returns naive datetimes and PostgreSQL aware ones; hash both as naive UTC.
    if moment.tzinfo is not None:
        moment = moment.astimezone(UTC).replace(tzinfo=None)
    return moment.isoformat(timespec="microseconds")


def _event_hash(event: models.AuditEvent) -> str:
    payload = {
        "project_id": event.project_id,
        "actor_id": event.actor_id,
        "action": event.action,
        "entity_type": event.entity_type,
        "entity_id": event.entity_id,
        "details": event.details,
        "created_at": _canonical_time(event.created_at),
        "prev_hash": event.prev_hash,
    }
    return hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()


def record_event(
    db: Session,
    *,
    project_id: int,
    actor_id: int | None,
    action: str,
    entity_type: str,
    entity_id: int | str | None = None,
    details: dict[str, Any] | None = None,
) -> models.AuditEvent:
    """Append an event to the project's chain. The caller commits it together with the change it describes."""
    # Serializes writers per project on PostgreSQL; SQLite already serializes writes.
    db.execute(select(models.Project.id).where(models.Project.id == project_id).with_for_update())
    previous = db.scalar(
        select(models.AuditEvent)
        .where(models.AuditEvent.project_id == project_id)
        .order_by(models.AuditEvent.id.desc())
        .limit(1)
    )
    event = models.AuditEvent(
        project_id=project_id,
        actor_id=actor_id,
        action=action,
        entity_type=entity_type,
        entity_id=None if entity_id is None else str(entity_id),
        details=details or {},
        created_at=models.utcnow(),
        prev_hash=previous.hash if previous else GENESIS_HASH,
    )
    event.hash = _event_hash(event)
    db.add(event)
    db.flush()
    # Queued in the same transaction as the change, so a webhook is never sent for a change that was rolled back.
    import webhooks

    webhooks.queue_deliveries(db, event)
    return event


def verify_chain(db: Session, project_id: int) -> bool:
    expected_prev_hash = GENESIS_HASH
    events = db.scalars(
        select(models.AuditEvent).where(models.AuditEvent.project_id == project_id).order_by(models.AuditEvent.id)
    )
    for event in events:
        if event.prev_hash != expected_prev_hash or event.hash != _event_hash(event):
            return False
        expected_prev_hash = event.hash
    return True
