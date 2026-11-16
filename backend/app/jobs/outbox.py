"""Outbox relay loop. Runs as a background thread inside the worker (``python -m app.jobs.worker``)."""

import logging
import threading

from sqlalchemy.orm import Session, sessionmaker

from app.ports.events import EventPublisher
from app.services.outbox import relay_pending

log = logging.getLogger("pra.outbox")


def relay_loop(session_factory: sessionmaker[Session], publisher: EventPublisher, stop: threading.Event,
               *, interval: float = 2.0) -> None:
    while not stop.is_set():
        try:
            with session_factory() as session:
                result = relay_pending(session, publisher)
            if result.published or result.failed:
                log.info("outbox relayed %d, failed %d", result.published, result.failed)
            if result.published:
                continue  # there may be more; don't wait
        except Exception:  # noqa: BLE001 - keep the loop alive; the DB may be restarting
            log.exception("outbox relay failed")
        stop.wait(interval)


def start_relay_thread(session_factory: sessionmaker[Session], publisher: EventPublisher,
                       *, interval: float = 2.0) -> tuple[threading.Thread, threading.Event]:
    stop = threading.Event()
    thread = threading.Thread(target=relay_loop, args=(session_factory, publisher, stop),
                              kwargs={"interval": interval}, name="outbox-relay", daemon=True)
    thread.start()
    return thread, stop
