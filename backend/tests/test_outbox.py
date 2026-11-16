import threading

from sqlalchemy import select

from app.adapters.local.events import LogEventPublisher
from app.db.models import OutboxEvent
from app.jobs.outbox import start_relay_thread
from app.services.outbox import MAX_ATTEMPTS, emit, relay_pending


def pending(session_factory, tenant):
    with session_factory() as s:
        return list(s.scalars(select(OutboxEvent).where(OutboxEvent.tenant_id == tenant,
                                                        OutboxEvent.published_at.is_(None))))


class Flaky:
    """Fails the first ``fail_times`` events of ``tenant``; other tenants' events go through."""

    def __init__(self, tenant: str, fail_times: int) -> None:
        self.tenant = tenant
        self.fail_times = fail_times
        self.published = []

    def publish(self, event):
        if event.tenant_id == self.tenant and self.fail_times:
            self.fail_times -= 1
            raise ConnectionError("bus down")
        self.published.append(event)


def drain(session_factory, publisher):
    """Relay until nothing more is published (other tests leave events behind in the shared DB)."""
    while True:
        with session_factory() as s:
            if relay_pending(s, publisher, batch=1000).published == 0:
                return


def test_events_only_exist_if_the_transaction_commits(session_factory, tenant):
    with session_factory() as s:
        emit(s, tenant_id=tenant, event_type="Test", aggregate_type="x", aggregate_id="1")
        s.rollback()
    with session_factory() as s:
        emit(s, tenant_id=tenant, event_type="Test", aggregate_type="x", aggregate_id="2", payload={"a": 1})
        s.commit()
    assert [e.aggregate_id for e in pending(session_factory, tenant)] == ["2"]


def test_relay_publishes_in_order_and_marks_done(session_factory, tenant):
    with session_factory() as s:
        for i in range(3):
            emit(s, tenant_id=tenant, event_type=f"E{i}", aggregate_type="x", aggregate_id=str(i))
        s.commit()
    publisher = LogEventPublisher()
    drain(session_factory, publisher)
    mine = [e for e in publisher.published if e.tenant_id == tenant]
    assert [e.event_type for e in mine] == ["E0", "E1", "E2"]
    assert len({e.event_id for e in mine}) == 3
    assert pending(session_factory, tenant) == []


def test_failed_publish_is_retried(session_factory, tenant):
    drain(session_factory, LogEventPublisher())  # clear what other tests left, so one pass sees only ours
    with session_factory() as s:
        emit(s, tenant_id=tenant, event_type="E", aggregate_type="x", aggregate_id="1")
        s.commit()
    flaky = Flaky(tenant, fail_times=1)
    with session_factory() as s:
        relay_pending(s, flaky, batch=1000)
    [row] = pending(session_factory, tenant)
    assert row.attempts == 1 and "bus down" in row.last_error
    drain(session_factory, flaky)
    assert pending(session_factory, tenant) == []
    assert [e.tenant_id for e in flaky.published].count(tenant) == 1


def test_gives_up_after_max_attempts(session_factory, tenant):
    with session_factory() as s:
        e = emit(s, tenant_id=tenant, event_type="E", aggregate_type="x", aggregate_id="1")
        e.attempts = MAX_ATTEMPTS
        s.commit()
    publisher = LogEventPublisher()
    drain(session_factory, publisher)
    assert all(ev.tenant_id != tenant for ev in publisher.published)


def test_relay_thread(session_factory, tenant):
    with session_factory() as s:
        emit(s, tenant_id=tenant, event_type="E", aggregate_type="x", aggregate_id="1")
        s.commit()
    publisher = LogEventPublisher()
    seen = threading.Event()
    original = publisher.publish

    def publish(event):
        original(event)
        if event.tenant_id == tenant:
            seen.set()

    publisher.publish = publish
    thread, stop = start_relay_thread(session_factory, publisher, interval=0.05)
    try:
        assert seen.wait(10)
    finally:
        stop.set()
        thread.join(5)
    assert not thread.is_alive()
