"""Background job: run the analysis workflow of a started run."""

import uuid

from app.jobs.evidence import _runtime
from app.jobs.progress import report_progress
from app.services import analysis


def analyse_run(analysis_id: str) -> dict:
    platform, session_factory = _runtime()
    report_progress(5, "Analysing")
    with session_factory() as session:
        a = analysis.run_analysis(session, platform, uuid.UUID(analysis_id))
        report_progress(100, a.status)
        return {"analysis_id": analysis_id, "status": a.status, "result_hash": a.result_hash}
