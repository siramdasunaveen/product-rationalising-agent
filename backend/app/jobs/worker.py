"""RQ worker entry point: ``python -m app.jobs.worker``.

Uses a non-forking worker with thread-based timeouts so it also runs on Windows (no fork/SIGALRM).
On Linux containers the same worker is fine; switch to the default forking ``Worker`` if needed.
"""

import logging

from redis import Redis
from rq import Queue, SimpleWorker
from rq.timeouts import TimerDeathPenalty

from app.adapters.local.jobs import QUEUE_NAME
from app.config import get_settings


class PortableWorker(SimpleWorker):
    death_penalty_class = TimerDeathPenalty


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    conn = Redis.from_url(get_settings().redis_url)
    PortableWorker([Queue(QUEUE_NAME, connection=conn)], connection=conn).work()


if __name__ == "__main__":
    main()
