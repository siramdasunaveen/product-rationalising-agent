from rq import get_current_job


def report_progress(progress: int, message: str = "") -> None:
    """Record progress (0..100) on the current job. No-op outside a job."""
    job = get_current_job()
    if job is None:
        return
    job.meta["progress"] = max(0, min(100, int(progress)))
    job.meta["message"] = message
    job.save_meta()
