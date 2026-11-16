import logging

from app.ports.events import DomainEvent

log = logging.getLogger("pra.events")


class LogEventPublisher:
    """Writes events to the log (and keeps them for tests). Studio may route them to its event bus."""

    def __init__(self) -> None:
        self.published: list[DomainEvent] = []

    def publish(self, event: DomainEvent) -> None:
        self.published.append(event)
        log.info("event %s tenant=%s %s/%s id=%s", event.event_type, event.tenant_id,
                 event.aggregate_type, event.aggregate_id, event.event_id)
