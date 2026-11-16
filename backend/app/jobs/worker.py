"""RQ worker entry point: ``python -m app.jobs.worker``.

Also relays the transactional outbox (domain events) in a background thread.

Uses a non-forking worker with thread-based timeouts so it also runs on Windows (no fork/SIGALRM).
On Linux containers the same worker is fine; switch to the default forking ``Worker`` if needed.
"""

import logging

from redis import Redis
from rq import Queue, SimpleWorker
from rq.timeouts import TimerDeathPenalty

from app.adapters.local.jobs import QUEUE_NAME
from app.adapters.registry import build_platform
from app.config import get_settings
from app.db.session import make_sessionmaker
from app.jobs.outbox import start_relay_thread


class PortableWorker(SimpleWorker):
    death_penalty_class = TimerDeathPenalty


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    settings = get_settings()
    conn = Redis.from_url(settings.redis_url)
    # Domain events: relay the transactional outbox while the worker runs.
    platform = build_platform(settings, redis=conn)
    start_relay_thread(make_sessionmaker(settings.database_url), platform.events)
    PortableWorker([Queue(QUEUE_NAME, connection=conn)], connection=conn).work()


if __name__ == "__main__":
    main()
