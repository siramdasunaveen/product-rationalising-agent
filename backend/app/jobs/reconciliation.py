"""Background job: reconcile the staged records of a run's sources."""

import uuid

from app.jobs.evidence import _runtime
from app.jobs.progress import report_progress
from app.services import reconciliation


def reconcile_run(reconciliation_id: str) -> dict:
    platform, session_factory = _runtime()
    report_progress(10, "Reconciling")
    with session_factory() as session:
        rec = reconciliation.run_reconciliation(session, platform, uuid.UUID(reconciliation_id))
        report_progress(100, rec.status)
        return {"reconciliation_id": reconciliation_id, "status": rec.status, "result_hash": rec.result_hash}
