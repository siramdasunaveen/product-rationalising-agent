"""reconciliation and evidence snapshots: several sources per domain, data-as-of date, reconciliation working set,
steward decisions (append-only), immutable snapshots with facts and relationships

Revision ID: 0007
Revises: 0006
Create Date: 2026-11-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"
J = postgresql.JSONB


def _fk(table: str, col: str, ref: str) -> sa.ForeignKeyConstraint:
    name = f"fk_{table}_{col}_{ref}"
    return sa.ForeignKeyConstraint([col], [f"{S}.{ref}.id"], name=name if len(name) <= 63 else f"fk_{table}_{col}"[:63])


def _uuid_base(created: bool = True) -> list[sa.Column]:
    cols = [sa.Column("id", sa.Uuid(), nullable=False), sa.Column("tenant_id", sa.String(64), nullable=False)]
    if created:
        cols.append(sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    return cols


def _big_base() -> list[sa.Column]:
    return [sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
            sa.Column("tenant_id", sa.String(64), nullable=False)]


def upgrade() -> None:
    # --- several sources per domain + data-as-of ---
    op.add_column("evidence_source", sa.Column("system", sa.String(40), nullable=False, server_default="EXCEL"),
                  schema=S)
    op.drop_constraint("uq_evidence_source_run_domain_type", "evidence_source", schema=S, type_="unique")
    op.create_unique_constraint("uq_evidence_source_run_domain_name", "evidence_source", ["run_id", "domain", "name"],
                                schema=S)
    op.add_column("evidence_upload", sa.Column("as_of_date", sa.Date(), nullable=True), schema=S)
    op.execute(f"UPDATE {S}.evidence_upload SET as_of_date = confirmed_at::date WHERE confirmed_at IS NOT NULL")

    # --- reconciliation working set ---
    op.create_table(
        "reconciliation_run", *_uuid_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("policy_ref", sa.String(80), nullable=False),
        sa.Column("policy_digest", sa.String(71), nullable=False),
        sa.Column("canonical_ref", sa.String(80), nullable=False),
        sa.Column("reconciled_on", sa.Date(), nullable=False),
        sa.Column("inputs", J(), nullable=False),
        sa.Column("counts", J(), nullable=True),
        sa.Column("summary", J(), nullable=True),
        sa.Column("result_hash", sa.String(71), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_run"), _fk("reconciliation_run", "run_id", "run"),
        schema=S,
    )
    op.create_index("ix_reconciliation_run_tenant_id", "reconciliation_run", ["tenant_id"], schema=S)
    op.create_index("ix_reconciliation_run_run_id", "reconciliation_run", ["run_id"], schema=S)

    op.create_table(
        "reconciled_record", *_uuid_base(created=False),
        sa.Column("reconciliation_id", sa.Uuid(), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("record_key", sa.String(512), nullable=False),
        sa.Column("values", J(), nullable=False),
        sa.Column("provenance", J(), nullable=False),
        sa.Column("sources", J(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reconciled_record"),
        sa.UniqueConstraint("reconciliation_id", "entity", "record_key", name="uq_reconciled_record_key"),
        _fk("reconciled_record", "reconciliation_id", "reconciliation_run"),
        schema=S,
    )
    op.create_index("ix_reconciled_record_tenant_id", "reconciled_record", ["tenant_id"], schema=S)
    op.create_index("ix_reconciled_record_reconciliation_id", "reconciled_record", ["reconciliation_id"], schema=S)

    op.create_table(
        "reconciled_relationship", *_big_base(),
        sa.Column("reconciliation_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("from_entity", sa.String(64), nullable=False),
        sa.Column("from_key", sa.String(512), nullable=False),
        sa.Column("to_entity", sa.String(64), nullable=False),
        sa.Column("to_key", sa.String(512), nullable=False),
        sa.Column("sources", J(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_reconciled_relationship"),
        _fk("reconciled_relationship", "reconciliation_id", "reconciliation_run"),
        schema=S,
    )
    op.create_index("ix_reconciled_relationship_tenant_id", "reconciled_relationship", ["tenant_id"], schema=S)
    op.create_index("ix_reconciled_relationship_reconciliation_id", "reconciled_relationship", ["reconciliation_id"],
                    schema=S)

    op.create_table(
        "reconciliation_conflict", *_uuid_base(created=False),
        sa.Column("reconciliation_id", sa.Uuid(), nullable=False),
        sa.Column("conflict_key", sa.String(32), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("record_key", sa.String(512), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("material", sa.Boolean(), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("candidates", J(), nullable=False),
        sa.Column("recommended_source_id", sa.String(64), nullable=False),
        sa.Column("reason", sa.Text(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_reconciliation_conflict"),
        sa.UniqueConstraint("reconciliation_id", "conflict_key", name="uq_reconciliation_conflict_key"),
        _fk("reconciliation_conflict", "reconciliation_id", "reconciliation_run"),
        schema=S,
    )
    op.create_index("ix_reconciliation_conflict_tenant_id", "reconciliation_conflict", ["tenant_id"], schema=S)
    op.create_index("ix_reconciliation_conflict_reconciliation_id", "reconciliation_conflict", ["reconciliation_id"],
                    schema=S)

    op.create_table(
        "steward_decision", *_uuid_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("conflict_key", sa.String(32), nullable=False),
        sa.Column("fingerprint", sa.String(32), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("record_key", sa.String(512), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("source_id", sa.String(64), nullable=True),
        sa.Column("value", J(), nullable=True),
        sa.Column("rationale", sa.Text(), nullable=True),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("actor_name", sa.String(120), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_steward_decision"), _fk("steward_decision", "run_id", "run"),
        sa.CheckConstraint("decision IN ('SELECT_SOURCE', 'MANUAL', 'DEFER')", name="ck_steward_decision_decision"),
        schema=S,
    )
    op.create_index("ix_steward_decision_tenant_id", "steward_decision", ["tenant_id"], schema=S)
    op.create_index("ix_steward_decision_conflict", "steward_decision", ["run_id", "conflict_key", "fingerprint"],
                    schema=S)

    # --- immutable snapshots ---
    op.create_table(
        "evidence_snapshot", *_uuid_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_no", sa.Integer(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("parent_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reconciliation_id", sa.Uuid(), nullable=False),
        sa.Column("policy_ref", sa.String(80), nullable=False),
        sa.Column("policy_digest", sa.String(71), nullable=False),
        sa.Column("canonical_ref", sa.String(80), nullable=False),
        sa.Column("inputs", J(), nullable=False),
        sa.Column("counts", J(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("approved_by", sa.String(128), nullable=False),
        sa.Column("approved_by_name", sa.String(120), nullable=False),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_snapshot"),
        sa.UniqueConstraint("run_id", "snapshot_no", name="uq_evidence_snapshot_number"),
        _fk("evidence_snapshot", "run_id", "run"),
        sa.ForeignKeyConstraint(["parent_snapshot_id"], [f"{S}.evidence_snapshot.id"],
                                name="fk_evidence_snapshot_parent_snapshot_id"),
        _fk("evidence_snapshot", "reconciliation_id", "reconciliation_run"),
        sa.CheckConstraint("status IN ('APPROVED', 'SUPERSEDED')", name="ck_evidence_snapshot_status"),
        sa.CheckConstraint("content_hash ~ '^sha256:[0-9a-f]{64}$'", name="ck_evidence_snapshot_content_hash"),
        schema=S,
    )
    op.create_index("ix_evidence_snapshot_tenant_id", "evidence_snapshot", ["tenant_id"], schema=S)
    op.create_index("ix_evidence_snapshot_run_id", "evidence_snapshot", ["run_id"], schema=S)
    op.execute(f"CREATE UNIQUE INDEX uq_evidence_snapshot_current ON {S}.evidence_snapshot (run_id) "
               "WHERE status = 'APPROVED'")

    op.create_table(
        "snapshot_fact", *_big_base(),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.String(20), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("record_key", sa.String(512), nullable=False),
        sa.Column("field", sa.String(64), nullable=False),
        sa.Column("value", J(), nullable=True),
        sa.Column("provenance", J(), nullable=False),
        sa.Column("stale", sa.Boolean(), nullable=False),
        sa.Column("conflicted", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_snapshot_fact"),
        sa.UniqueConstraint("snapshot_id", "evidence_id", name="uq_snapshot_fact_evidence_id"),
        _fk("snapshot_fact", "snapshot_id", "evidence_snapshot"),
        schema=S,
    )
    op.create_index("ix_snapshot_fact_tenant_id", "snapshot_fact", ["tenant_id"], schema=S)
    op.create_index("ix_snapshot_fact_record", "snapshot_fact", ["snapshot_id", "entity", "record_key"], schema=S)

    op.create_table(
        "snapshot_relationship", *_big_base(),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("evidence_id", sa.String(20), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("from_entity", sa.String(64), nullable=False),
        sa.Column("from_key", sa.String(512), nullable=False),
        sa.Column("to_entity", sa.String(64), nullable=False),
        sa.Column("to_key", sa.String(512), nullable=False),
        sa.Column("sources", J(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_snapshot_relationship"),
        _fk("snapshot_relationship", "snapshot_id", "evidence_snapshot"),
        schema=S,
    )
    op.create_index("ix_snapshot_relationship_tenant_id", "snapshot_relationship", ["tenant_id"], schema=S)
    op.create_index("ix_snapshot_relationship_from", "snapshot_relationship", ["snapshot_id", "from_entity", "from_key"],
                    schema=S)
    op.create_index("ix_snapshot_relationship_to", "snapshot_relationship", ["snapshot_id", "to_entity", "to_key"],
                    schema=S)

    # Snapshots: everything frozen except APPROVED -> SUPERSEDED; never deleted.
    op.execute(f"""
        CREATE FUNCTION "{S}".evidence_snapshot_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'evidence snapshots are never deleted (% blocked)', TG_OP
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF (to_jsonb(NEW) - 'status') IS DISTINCT FROM (to_jsonb(OLD) - 'status') THEN
                RAISE EXCEPTION 'evidence snapshot % is immutable; approve a new snapshot instead', OLD.code
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.status <> OLD.status AND NOT (OLD.status = 'APPROVED' AND NEW.status = 'SUPERSEDED') THEN
                RAISE EXCEPTION 'evidence snapshot status cannot change from % to %', OLD.status, NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute(f"""CREATE TRIGGER evidence_snapshot_guard_rows BEFORE UPDATE OR DELETE ON "{S}".evidence_snapshot
                   FOR EACH ROW EXECUTE FUNCTION "{S}".evidence_snapshot_guard();""")
    op.execute(f"""CREATE TRIGGER evidence_snapshot_guard_truncate BEFORE TRUNCATE ON "{S}".evidence_snapshot
                   FOR EACH STATEMENT EXECUTE FUNCTION "{S}".evidence_snapshot_guard();""")
    for table in ("snapshot_fact", "snapshot_relationship", "steward_decision"):
        op.execute(f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON "{S}".{table}
                       FOR EACH ROW EXECUTE FUNCTION "{S}".append_only();""")


def downgrade() -> None:
    for table in ("snapshot_fact", "snapshot_relationship", "steward_decision"):
        op.execute(f'DROP TRIGGER IF EXISTS {table}_append_only ON "{S}".{table}')
    op.execute(f'DROP TRIGGER IF EXISTS evidence_snapshot_guard_truncate ON "{S}".evidence_snapshot')
    op.execute(f'DROP TRIGGER IF EXISTS evidence_snapshot_guard_rows ON "{S}".evidence_snapshot')
    op.execute(f'DROP FUNCTION IF EXISTS "{S}".evidence_snapshot_guard()')
    for table in ("snapshot_relationship", "snapshot_fact", "evidence_snapshot", "steward_decision",
                  "reconciliation_conflict", "reconciled_relationship", "reconciled_record", "reconciliation_run"):
        op.drop_table(table, schema=S)
    op.drop_column("evidence_upload", "as_of_date", schema=S)
    op.drop_constraint("uq_evidence_source_run_domain_name", "evidence_source", schema=S, type_="unique")
    op.create_unique_constraint("uq_evidence_source_run_domain_type", "evidence_source",
                                ["run_id", "domain", "source_type"], schema=S)
    op.drop_column("evidence_source", "system", schema=S)
