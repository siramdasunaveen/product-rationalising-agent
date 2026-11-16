from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Protocol


@dataclass(frozen=True)
class AuditRecord:
    """What happened. Who/when/hash are filled in by the audit log."""

    tenant_id: str
    actor_id: str
    action: str  # e.g. "run.created", "decision.submitted"
    entity_type: str
    entity_id: str
    actor_roles: tuple[str, ...] = ()
    auth_context: dict[str, Any] = field(default_factory=dict)
    # Free payload: decision version, policy result, content hash of the decided object, etc.
    payload: dict[str, Any] = field(default_factory=dict)
    note: str | None = None


@dataclass(frozen=True)
class AuditEntry:
    seq: int
    occurred_at: datetime
    hash: str
    prev_hash: str


@dataclass(frozen=True)
class ChainCheck:
    ok: bool
    checked: int
    broken_at_seq: int | None = None
    reason: str | None = None


class AuditLog(Protocol):
    def record(self, record: AuditRecord, *, session: Any = None) -> AuditEntry:
        """Append an event. The local adapter writes in the caller's DB session (same transaction)."""
        ...

    def verify_chain(self, tenant_id: str, *, session: Any = None) -> ChainCheck: ...
