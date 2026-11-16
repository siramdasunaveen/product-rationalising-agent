from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class DomainEvent:
    """A fact that happened, e.g. ``ArtifactVersionPublished``. Delivered at least once (dedupe by id)."""

    event_id: str
    tenant_id: str
    event_type: str
    aggregate_type: str
    aggregate_id: str
    occurred_at: datetime
    payload: dict[str, Any] = field(default_factory=dict)


class EventPublisher(Protocol):
    def publish(self, event: DomainEvent) -> None:
        """Deliver one event. Raise to have the outbox retry it later."""
        ...
