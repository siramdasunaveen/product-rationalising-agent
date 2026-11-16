"""Transactional outbox: domain events are written in the same transaction as the change they
describe, then relayed to the ``EventPublisher`` by a polling loop (``app.jobs.outbox``).

At-least-once delivery: an event can be published twice if the relay stops between publishing and
marking it; consumers dedupe by ``event_id``.
"""

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import OutboxEvent
from app.ports.events import DomainEvent, EventPublisher

MAX_ATTEMPTS = 10


def emit(session: Session, *, tenant_id: str, event_type: str, aggregate_type: str, aggregate_id: str,
         payload: dict[str, Any] | None = None) -> OutboxEvent:
    """Add an event to the caller's transaction. It is only relayed if that transaction commits."""
    event = OutboxEvent(tenant_id=tenant_id, event_type=event_type, aggregate_type=aggregate_type,
                        aggregate_id=aggregate_id, payload=payload or {}, attempts=0)
    session.add(event)
    return event


@dataclass(frozen=True)
class RelayResult:
    published: int
    failed: int


def _to_domain(e: OutboxEvent) -> DomainEvent:
    return DomainEvent(event_id=str(e.event_id), tenant_id=e.tenant_id, event_type=e.event_type,
                       aggregate_type=e.aggregate_type, aggregate_id=e.aggregate_id,
                       occurred_at=e.occurred_at, payload=dict(e.payload))


def relay_pending(session: Session, publisher: EventPublisher, *, batch: int = 100) -> RelayResult:
    """Publish up to ``batch`` pending events in order. Safe to run in several workers at once."""
    rows = list(session.scalars(
        select(OutboxEvent)
        .where(OutboxEvent.published_at.is_(None), OutboxEvent.attempts < MAX_ATTEMPTS)
        .order_by(OutboxEvent.id).limit(batch).with_for_update(skip_locked=True)))
    published = failed = 0
    for row in rows:
        try:
            publisher.publish(_to_domain(row))
        except Exception as exc:  # noqa: BLE001 - any publisher failure is retried later
            row.attempts += 1
            row.last_error = f"{type(exc).__name__}: {exc}"[:2000]
            failed += 1
        else:
            row.attempts += 1
            row.published_at = datetime.now(UTC)
            row.last_error = None
            published += 1
    session.commit()
    return RelayResult(published, failed)
