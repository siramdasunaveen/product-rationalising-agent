from dataclasses import dataclass
from enum import StrEnum
from typing import Any, Protocol


class JobState(StrEnum):
    QUEUED = "queued"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class JobStatus:
    job_id: str
    state: JobState
    progress: int = 0  # 0..100
    message: str = ""
    error: str | None = None


class JobQueue(Protocol):
    def enqueue(self, func_path: str, *args: Any, job_id: str | None = None, **kwargs: Any) -> str:
        """Queue ``func_path`` (dotted path, e.g. ``app.jobs.ping.ping``). Returns the job id."""
        ...

    def status(self, job_id: str) -> JobStatus: ...
