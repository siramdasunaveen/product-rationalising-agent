"""Security event sink: structured records on the ``pra.security`` logger, meant to be shipped to the SIEM.

Use for authentication failures, access denials, separation-of-duties refusals, lockouts, malware detections,
secret and configuration changes, and anything an operator should be alerted on. Never pass secrets, tokens,
passwords or evidence values; identify people by id, or by ``pseudonym(email)`` before they are authenticated.
"""

import hashlib
import logging
from typing import Any

from app.core import request_context

log = logging.getLogger("pra.security")

LEVELS = {"INFO": logging.INFO, "WARNING": logging.WARNING, "ERROR": logging.ERROR, "CRITICAL": logging.CRITICAL}


def pseudonym(value: str) -> str:
    """Stable, non-reversible identifier for an email or user name typed by an unauthenticated caller."""
    return "sha256:" + hashlib.sha256(value.strip().lower().encode("utf-8")).hexdigest()[:16]


def security_event(kind: str, severity: str = "WARNING", **fields: Any) -> dict[str, Any]:
    """Emit one structured security event and return its payload (for tests)."""
    ctx = request_context.current()
    payload: dict[str, Any] = {
        "event": kind,
        "severity": severity,
        "request_id": ctx.request_id,
        "client_ip": ctx.client_ip,
        "tenant_id": fields.pop("tenant_id", None) or ctx.tenant_id,
        "user_id": fields.pop("user_id", None) or ctx.user_id,
        "job_id": ctx.job_id,
        **fields,
    }
    log.log(LEVELS.get(severity, logging.WARNING), kind, extra={"security": payload})
    return payload
