import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError

from app.adapters.local.audit import GENESIS_HASH, DbAuditLog
from app.ports.audit import AuditRecord


def _rec(tenant: str, action: str = "run.created", **payload) -> AuditRecord:
    return AuditRecord(
        tenant_id=tenant, actor_id="u1", actor_roles=("portfolio_manager",),
        auth_context={"method": "password", "jti": "abc"}, action=action,
        entity_type="run", entity_id="RUN-1", payload=payload or {"decision_version": 1},
    )


def test_chain_links_events(db, tenant):
    log = DbAuditLog()
    e1 = log.record(_rec(tenant), session=db)
    e2 = log.record(_rec(tenant, "run.updated", nested={"a": [1, 2]}), session=db)
    db.commit()

    assert (e1.seq, e2.seq) == (1, 2)
    assert e1.prev_hash == GENESIS_HASH
    assert e2.prev_hash == e1.hash
    check = log.verify_chain(tenant, session=db)
    assert check.ok and check.checked == 2


def test_chains_are_per_tenant(db, tenant):
    log = DbAuditLog()
    log.record(_rec(tenant), session=db)
    other = log.record(_rec(tenant + "-b"), session=db)
    db.commit()
    assert other.seq == 1 and other.prev_hash == GENESIS_HASH


def test_rolled_back_event_leaves_chain_intact(db, tenant):
    log = DbAuditLog()
    log.record(_rec(tenant), session=db)
    db.commit()
    log.record(_rec(tenant, "will.rollback"), session=db)
    db.rollback()
    e = log.record(_rec(tenant, "after"), session=db)
    db.commit()
    assert e.seq == 2
    assert log.verify_chain(tenant, session=db).ok


@pytest.mark.parametrize("sql", [
    "UPDATE rationalisation.audit_event SET action = 'x' WHERE tenant_id = :t",
    "DELETE FROM rationalisation.audit_event WHERE tenant_id = :t",
    "TRUNCATE rationalisation.audit_event",
])
def test_database_blocks_changes(db, tenant, sql):
    DbAuditLog().record(_rec(tenant), session=db)
    db.commit()
    with pytest.raises(DBAPIError, match="append-only"):
        db.execute(text(sql), {"t": tenant})
    db.rollback()


def test_tampering_is_detected(db, tenant):
    """Simulate a privileged edit that bypasses the trigger; verify_chain must catch it."""
    log = DbAuditLog()
    for i in range(3):
        log.record(_rec(tenant, f"step.{i}"), session=db)
    db.commit()

    db.execute(text("ALTER TABLE rationalisation.audit_event DISABLE TRIGGER audit_event_no_update_delete"))
    db.execute(text("UPDATE rationalisation.audit_event SET payload = '{\"decision_version\": 99}' "
                    "WHERE tenant_id = :t AND seq = 2"), {"t": tenant})
    db.execute(text("ALTER TABLE rationalisation.audit_event ENABLE TRIGGER audit_event_no_update_delete"))
    db.commit()

    check = log.verify_chain(tenant, session=db)
    assert not check.ok
    assert check.broken_at_seq == 2
    assert check.checked == 1
