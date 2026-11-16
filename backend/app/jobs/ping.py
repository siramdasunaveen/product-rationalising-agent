"""Smoke-test job: proves the queue, worker and progress reporting work."""

from app.jobs.progress import report_progress


def ping(message: str = "pong") -> dict:
    report_progress(50, "halfway")
    report_progress(100, "done")
    return {"message": message}
