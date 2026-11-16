"""Append-only audit log in Postgres with a per-tenant SHA-256 hash chain.

Each event's hash covers its content and the previous event's hash, so any edit or deletion
breaks the chain and ``verify_chain`` reports where. A DB trigger additionally blocks
UPDATE/DELETE/TRUNCATE on the table.
"""

import hashlib
import json
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.db.models import AuditEvent
from app.ports.audit import AuditEntry, AuditRecord, ChainCheck

GENESIS_HASH = "0" * 64


def _jsonable(value: Any) -> Any:
    """Round-trip through JSON so what we hash is exactly what JSONB stores and returns."""
    return json.loads(json.dumps(value, default=str))


def compute_hash(*, tenant_id: str, seq: int, occurred_at: datetime, actor_id: str,
                 actor_roles: list[str], auth_context: dict, action: str, entity_type: str,
                 entity_id: str, payload: dict, note: str | None, prev_hash: str) -> str:
    body = {
        "tenant_id": tenant_id,
        "seq": seq,
        "occurred_at": occurred_at.astimezone(UTC).isoformat(),
        "actor_id": actor_id,
        "actor_roles": actor_roles,
        "auth_context": auth_context,
        "action": action,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "payload": payload,
        "note": note,
        "prev_hash": prev_hash,
    }
    canonical = json.dumps(body, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def _event_hash(e: AuditEvent) -> str:
    return compute_hash(
        tenant_id=e.tenant_id, seq=e.seq, occurred_at=e.occurred_at, actor_id=e.actor_id,
        actor_roles=list(e.actor_roles), auth_context=e.auth_context, action=e.action,
        entity_type=e.entity_type, entity_id=e.entity_id, payload=e.payload, note=e.note,
        prev_hash=e.prev_hash,
    )


class DbAuditLog:
    def record(self, record: AuditRecord, *, session: Session | None = None) -> AuditEntry:
        if session is None:
            raise ValueError("DbAuditLog.record needs the caller's DB session")
        # Serialise appends per tenant for the rest of this transaction.
        session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:t, 0))"),
                        {"t": f"audit:{record.tenant_id}"})
        last = session.execute(
            select(AuditEvent.seq, AuditEvent.hash)
            .where(AuditEvent.tenant_id == record.tenant_id)
            .order_by(AuditEvent.seq.desc())
            .limit(1)
        ).first()
        seq = (last.seq + 1) if last else 1
        prev_hash = last.hash if last else GENESIS_HASH

        event = AuditEvent(
            tenant_id=record.tenant_id,
            seq=seq,
            occurred_at=datetime.now(UTC),
            actor_id=record.actor_id,
            actor_roles=list(record.actor_roles),
            auth_context=_jsonable(record.auth_context),
            action=record.action,
            entity_type=record.entity_type,
            entity_id=record.entity_id,
            payload=_jsonable(record.payload),
            note=record.note,
            prev_hash=prev_hash,
            hash="",
        )
        event.hash = _event_hash(event)
        session.add(event)
        session.flush()
        return AuditEntry(seq=seq, occurred_at=event.occurred_at, hash=event.hash, prev_hash=prev_hash)

    def verify_chain(self, tenant_id: str, *, session: Session | None = None) -> ChainCheck:
        if session is None:
            raise ValueError("DbAuditLog.verify_chain needs a DB session")
        events = session.execute(
            select(AuditEvent).where(AuditEvent.tenant_id == tenant_id).order_by(AuditEvent.seq)
        ).scalars()
        prev_hash, expected_seq, checked = GENESIS_HASH, 1, 0
        for e in events:
            if e.seq != expected_seq:
                return ChainCheck(False, checked, e.seq, f"gap: expected seq {expected_seq}")
            if e.prev_hash != prev_hash:
                return ChainCheck(False, checked, e.seq, "prev_hash does not match previous event")
            if _event_hash(e) != e.hash:
                return ChainCheck(False, checked, e.seq, "content does not match hash")
            prev_hash, expected_seq, checked = e.hash, expected_seq + 1, checked + 1
        return ChainCheck(True, checked)
