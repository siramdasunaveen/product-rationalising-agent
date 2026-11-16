"""Background jobs on Redis + RQ. Progress is kept in ``job.meta`` (see ``app.jobs.progress``)."""

from typing import Any

from redis import Redis
from rq import Queue
from rq.exceptions import NoSuchJobError
from rq.job import Job

from app.ports.jobs import JobState, JobStatus

QUEUE_NAME = "pra"

_STATE = {
    "queued": JobState.QUEUED,
    "deferred": JobState.QUEUED,
    "scheduled": JobState.QUEUED,
    "started": JobState.RUNNING,
    "finished": JobState.SUCCEEDED,
    "failed": JobState.FAILED,
    "stopped": JobState.FAILED,
    "canceled": JobState.FAILED,
}


class RqJobQueue:
    def __init__(self, connection: Redis, *, is_async: bool = True) -> None:
        self._conn = connection
        self._queue = Queue(QUEUE_NAME, connection=connection, is_async=is_async)

    def enqueue(self, func_path: str, *args: Any, job_id: str | None = None, **kwargs: Any) -> str:
        job = self._queue.enqueue(func_path, *args, job_id=job_id, **kwargs)
        return job.id

    def status(self, job_id: str) -> JobStatus:
        try:
            job = Job.fetch(job_id, connection=self._conn)
        except NoSuchJobError:
            return JobStatus(job_id=job_id, state=JobState.UNKNOWN)
        state = _STATE.get(str(job.get_status(refresh=True).value), JobState.UNKNOWN)
        meta = job.get_meta(refresh=True)
        progress = 100 if state == JobState.SUCCEEDED else int(meta.get("progress", 0))
        error = None
        if state == JobState.FAILED:
            result = job.latest_result()
            error = (result.exc_string or "").strip().splitlines()[-1:] if result else None
            error = error[0] if error else "Job failed"
        return JobStatus(job_id=job_id, state=state, progress=progress,
                         message=str(meta.get("message", "")), error=error)
