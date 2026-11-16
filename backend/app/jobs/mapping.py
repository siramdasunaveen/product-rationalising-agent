"""Background jobs for Mapping Rules: validate a version against its file; apply a published version."""

import uuid

from app.config import get_settings
from app.jobs.evidence import _runtime
from app.jobs.progress import report_progress
from app.services import evidence, mapping


def validate_mapping(validation_id: str) -> dict:
    platform, session_factory = _runtime()
    report_progress(10, "Validating")
    with session_factory() as session:
        run = mapping.run_validation(session, platform, evidence.limits_from(get_settings()), uuid.UUID(validation_id))
        report_progress(100, run.status)
        return {"validation_id": validation_id, "status": run.status}


def execute_mapping(execution_id: str) -> dict:
    platform, session_factory = _runtime()
    report_progress(10, "Applying mapping")
    with session_factory() as session:
        ex = mapping.run_execution(session, platform, evidence.limits_from(get_settings()), uuid.UUID(execution_id))
        report_progress(100, ex.status)
        return {"execution_id": execution_id, "status": ex.status, "output_hash": ex.output_hash}
