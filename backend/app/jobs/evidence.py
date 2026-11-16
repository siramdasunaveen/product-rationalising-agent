"""Background job: scan, check and read a quarantined evidence upload."""

import uuid
from functools import lru_cache

from app.adapters.registry import Platform, build_platform
from app.config import get_settings
from app.db.session import make_sessionmaker
from app.jobs.progress import report_progress
from app.services import evidence


@lru_cache
def _runtime() -> tuple[Platform, object]:
    settings = get_settings()
    return build_platform(settings), make_sessionmaker(settings.database_url)


def process_upload(upload_id: str) -> dict:
    platform, session_factory = _runtime()
    report_progress(10, "Scanning")
    with session_factory() as session:
        upload = evidence.process_upload(session, platform, evidence.limits_from(get_settings()),
                                         uuid.UUID(upload_id))
        report_progress(100, upload.status)
        return {"upload_id": upload_id, "status": upload.status}
