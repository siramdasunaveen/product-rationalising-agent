"""mapping rules: definitions, versions, rules, validation runs, approvals, executions, staged output, lineage

Immutability in the database:
- mapping versions are never deleted; status changes follow the lifecycle; the pinned source schema,
  canonical schema and content hash change only while the version is editable (DRAFT,
  VALIDATION_FAILED, READY_FOR_REVIEW) or when a reviewer returns it to DRAFT;
- rules can be inserted, changed or deleted only while their version is editable;
- approvals, staged records, staged relationships and field lineage are append-only.

Revision ID: 0006
Revises: 0005
Create Date: 2026-11-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"
EDITABLE = "('DRAFT', 'VALIDATION_FAILED', 'READY_FOR_REVIEW')"
# Keep in sync with app.domain.mapping.TRANSITIONS (tests check every pair).
TRANSITIONS = [
    ("DRAFT", "VALIDATING"),
    ("VALIDATING", "VALIDATION_FAILED"), ("VALIDATING", "READY_FOR_REVIEW"), ("VALIDATING", "DRAFT"),
    ("VALIDATION_FAILED", "DRAFT"), ("VALIDATION_FAILED", "VALIDATING"),
    ("READY_FOR_REVIEW", "DRAFT"), ("READY_FOR_REVIEW", "VALIDATING"), ("READY_FOR_REVIEW", "IN_REVIEW"),
    ("IN_REVIEW", "APPROVED"), ("IN_REVIEW", "DRAFT"), ("IN_REVIEW", "RETIRED"),
    ("APPROVED", "PUBLISHED"), ("APPROVED", "RETIRED"),
    ("PUBLISHED", "DEPRECATED"), ("PUBLISHED", "SUPERSEDED"), ("PUBLISHED", "RETIRED"),
    ("DEPRECATED", "SUPERSEDED"), ("DEPRECATED", "RETIRED"),
    ("SUPERSEDED", "RETIRED"),
]
APPEND_ONLY = ("mapping_approval", "staged_record", "staged_relationship", "field_lineage")


def _base(with_created: bool = True) -> list[sa.Column]:
    cols = [sa.Column("id", sa.Uuid(), nullable=False), sa.Column("tenant_id", sa.String(64), nullable=False)]
    if with_created:
        cols.append(sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False))
    return cols


def _fk(table: str, col: str, ref: str) -> sa.ForeignKeyConstraint:
    name = f"fk_{table}_{col}_{ref}"
    if len(name) > 63:  # Postgres identifier limit
        name = f"fk_{table}_{col}"[:63]
    return sa.ForeignKeyConstraint([col], [f"{S}.{ref}.id"], name=name)


def upgrade() -> None:
    op.create_table(
        "mapping_definition", *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_mapping_definition"),
        sa.UniqueConstraint("source_id", name="uq_mapping_definition_source"),
        _fk("mapping_definition", "run_id", "run"), _fk("mapping_definition", "source_id", "evidence_source"),
        schema=S,
    )
    op.create_index("ix_mapping_definition_tenant_id", "mapping_definition", ["tenant_id"], schema=S)
    op.create_index("ix_mapping_definition_run_id", "mapping_definition", ["run_id"], schema=S)

    op.create_table(
        "mapping_version", *_base(),
        sa.Column("definition_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("source_schema_version_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_ref", sa.String(80), nullable=False),
        sa.Column("canonical_digest", sa.String(71), nullable=False),
        sa.Column("status", sa.String(24), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=True),
        sa.Column("warnings_acknowledged", sa.Boolean(), nullable=False),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("submitted_by", sa.String(128), nullable=True),
        sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("approved_by", sa.String(128), nullable=True),
        sa.Column("approved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(128), nullable=True),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_mapping_version"),
        sa.UniqueConstraint("definition_id", "version", name="uq_mapping_version_number"),
        _fk("mapping_version", "definition_id", "mapping_definition"),
        sa.ForeignKeyConstraint(["parent_version_id"], [f"{S}.mapping_version.id"],
                                name="fk_mapping_version_parent_version_id_mapping_version"),
        _fk("mapping_version", "source_schema_version_id", "source_schema_version"),
        schema=S,
    )
    op.create_index("ix_mapping_version_tenant_id", "mapping_version", ["tenant_id"], schema=S)
    op.create_index("ix_mapping_version_definition_id", "mapping_version", ["definition_id"], schema=S)
    op.execute(f"CREATE UNIQUE INDEX uq_mapping_version_published ON {S}.mapping_version (definition_id) "
               "WHERE status = 'PUBLISHED'")
    op.execute(f"CREATE UNIQUE INDEX uq_mapping_version_open ON {S}.mapping_version (definition_id) "
               "WHERE status IN ('DRAFT', 'VALIDATING', 'VALIDATION_FAILED', 'READY_FOR_REVIEW')")

    op.create_table(
        "mapping_rule", *_base(with_created=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("rule_key", sa.String(16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("mapping_type", sa.String(16), nullable=False),
        sa.Column("source_column", sa.String(255), nullable=True),
        sa.Column("target_entity", sa.String(64), nullable=True),
        sa.Column("target_field", sa.String(64), nullable=True),
        sa.Column("params", postgresql.JSONB(), nullable=False),
        sa.Column("required", sa.Boolean(), nullable=False),
        sa.Column("null_handling", sa.String(16), nullable=False),
        sa.Column("default_value", sa.String(255), nullable=True),
        sa.Column("suggestion_confidence", sa.Integer(), nullable=True),
        sa.Column("proposed_by", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_mapping_rule"),
        sa.UniqueConstraint("version_id", "rule_key", name="uq_mapping_rule_key"),
        _fk("mapping_rule", "version_id", "mapping_version"),
        schema=S,
    )
    op.create_index("ix_mapping_rule_tenant_id", "mapping_rule", ["tenant_id"], schema=S)
    op.create_index("ix_mapping_rule_version_id", "mapping_rule", ["version_id"], schema=S)

    op.create_table(
        "mapping_validation_run", *_base(),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("result", postgresql.JSONB(), nullable=True),
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_mapping_validation_run"),
        _fk("mapping_validation_run", "version_id", "mapping_version"),
        _fk("mapping_validation_run", "upload_id", "evidence_upload"),
        schema=S,
    )
    op.create_index("ix_mapping_validation_run_tenant_id", "mapping_validation_run", ["tenant_id"], schema=S)
    op.create_index("ix_mapping_validation_run_version_id", "mapping_validation_run", ["version_id"], schema=S)

    op.create_table(
        "mapping_approval", *_base(),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("decision", sa.String(16), nullable=False),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("actor_name", sa.String(120), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_mapping_approval"),
        _fk("mapping_approval", "version_id", "mapping_version"),
        sa.CheckConstraint("decision IN ('APPROVE', 'RETURN', 'REJECT')", name="ck_mapping_approval_decision"),
        schema=S,
    )
    op.create_index("ix_mapping_approval_tenant_id", "mapping_approval", ["tenant_id"], schema=S)
    op.create_index("ix_mapping_approval_version_id", "mapping_approval", ["version_id"], schema=S)

    op.create_table(
        "mapping_execution", *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("mapping_version_id", sa.Uuid(), nullable=False),
        sa.Column("execution_key", sa.String(71), nullable=False),
        sa.Column("idempotency_key", sa.String(128), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column("counts", postgresql.JSONB(), nullable=True),
        sa.Column("rejected_rows", postgresql.JSONB(), nullable=True),
        sa.Column("conflicts", postgresql.JSONB(), nullable=True),
        sa.Column("output_hash", sa.String(71), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_mapping_execution"),
        sa.UniqueConstraint("tenant_id", "execution_key", name="uq_mapping_execution_key"),
        sa.UniqueConstraint("tenant_id", "idempotency_key", name="uq_mapping_execution_idem"),
        _fk("mapping_execution", "run_id", "run"), _fk("mapping_execution", "upload_id", "evidence_upload"),
        _fk("mapping_execution", "mapping_version_id", "mapping_version"),
        schema=S,
    )
    op.create_index("ix_mapping_execution_tenant_id", "mapping_execution", ["tenant_id"], schema=S)
    op.create_index("ix_mapping_execution_run_id", "mapping_execution", ["run_id"], schema=S)

    op.create_table(
        "staged_record", *_base(with_created=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("entity", sa.String(64), nullable=False),
        sa.Column("record_key", sa.String(512), nullable=False),
        sa.Column("values", postgresql.JSONB(), nullable=False),
        sa.Column("source_rows", postgresql.ARRAY(sa.Integer()), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_staged_record"),
        sa.UniqueConstraint("execution_id", "entity", "record_key", name="uq_staged_record_key"),
        _fk("staged_record", "execution_id", "mapping_execution"),
        schema=S,
    )
    op.create_index("ix_staged_record_tenant_id", "staged_record", ["tenant_id"], schema=S)
    op.create_index("ix_staged_record_execution_id", "staged_record", ["execution_id"], schema=S)

    op.create_table(
        "staged_relationship",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("from_entity", sa.String(64), nullable=False),
        sa.Column("from_key", sa.String(512), nullable=False),
        sa.Column("to_entity", sa.String(64), nullable=False),
        sa.Column("to_key", sa.String(512), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("rule_key", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_staged_relationship"),
        _fk("staged_relationship", "execution_id", "mapping_execution"),
        schema=S,
    )
    op.create_index("ix_staged_relationship_tenant_id", "staged_relationship", ["tenant_id"], schema=S)
    op.create_index("ix_staged_relationship_execution_id", "staged_relationship", ["execution_id"], schema=S)

    op.create_table(
        "field_lineage",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("execution_id", sa.Uuid(), nullable=False),
        sa.Column("staged_record_id", sa.Uuid(), nullable=False),
        sa.Column("canonical_entity", sa.String(64), nullable=False),
        sa.Column("canonical_field", sa.String(64), nullable=False),
        sa.Column("canonical_value", sa.Text(), nullable=True),
        sa.Column("source_system", sa.String(64), nullable=False),
        sa.Column("source_key", sa.String(512), nullable=False),
        sa.Column("source_field", sa.String(255), nullable=True),
        sa.Column("source_value", sa.Text(), nullable=True),
        sa.Column("upload_id", sa.Uuid(), nullable=False),
        sa.Column("sheet", sa.String(64), nullable=False),
        sa.Column("source_row", sa.Integer(), nullable=False),
        sa.Column("mapping_rule_key", sa.String(16), nullable=False),
        sa.Column("mapping_type", sa.String(16), nullable=False),
        sa.Column("lookup_ref", sa.String(80), nullable=True),
        sa.Column("transform_ref", sa.String(80), nullable=True),
        sa.Column("crosswalk_ref", sa.String(80), nullable=True),
        sa.Column("mapping_version_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_field_lineage"),
        _fk("field_lineage", "execution_id", "mapping_execution"),
        _fk("field_lineage", "staged_record_id", "staged_record"),
        schema=S,
    )
    op.create_index("ix_field_lineage_tenant_id", "field_lineage", ["tenant_id"], schema=S)
    op.create_index("ix_field_lineage_execution_id", "field_lineage", ["execution_id"], schema=S)
    op.create_index("ix_field_lineage_staged_record_id", "field_lineage", ["staged_record_id"], schema=S)

    allowed = " OR ".join(f"(OLD.status = '{a}' AND NEW.status = '{b}')" for a, b in TRANSITIONS)
    op.execute(f"""
        CREATE FUNCTION "{S}".mapping_version_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP IN ('DELETE', 'TRUNCATE') THEN
                RAISE EXCEPTION 'mapping versions are never deleted (% blocked)', TG_OP
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.definition_id <> OLD.definition_id
               OR NEW.version <> OLD.version OR NEW.parent_version_id IS DISTINCT FROM OLD.parent_version_id
               OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'the identity of a mapping version cannot change'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.status NOT IN {EDITABLE} AND NOT (OLD.status = 'IN_REVIEW' AND NEW.status = 'DRAFT') AND (
                   NEW.source_schema_version_id <> OLD.source_schema_version_id
                   OR NEW.canonical_ref <> OLD.canonical_ref OR NEW.canonical_digest <> OLD.canonical_digest
                   OR NEW.content_hash IS DISTINCT FROM OLD.content_hash) THEN
                RAISE EXCEPTION 'mapping version % is % and its content is frozen', OLD.version, OLD.status
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.status <> OLD.status AND NOT ({allowed}) THEN
                RAISE EXCEPTION 'mapping version status cannot change from % to %', OLD.status, NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute(f"""CREATE TRIGGER mapping_version_guard_rows BEFORE UPDATE OR DELETE ON "{S}".mapping_version
                   FOR EACH ROW EXECUTE FUNCTION "{S}".mapping_version_guard();""")
    op.execute(f"""CREATE TRIGGER mapping_version_guard_truncate BEFORE TRUNCATE ON "{S}".mapping_version
                   FOR EACH STATEMENT EXECUTE FUNCTION "{S}".mapping_version_guard();""")

    op.execute(f"""
        CREATE FUNCTION "{S}".mapping_rule_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        DECLARE v_status text;
        BEGIN
            SELECT status INTO v_status FROM "{S}".mapping_version
             WHERE id = CASE WHEN TG_OP = 'DELETE' THEN OLD.version_id ELSE NEW.version_id END;
            IF v_status IS NULL OR v_status NOT IN {EDITABLE} THEN
                RAISE EXCEPTION 'rules of a % mapping version cannot change (% blocked)', v_status, TG_OP
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF TG_OP = 'UPDATE' AND NEW.version_id <> OLD.version_id THEN
                RAISE EXCEPTION 'a rule cannot move to another version' USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END;
        $$;
    """)
    op.execute(f"""CREATE TRIGGER mapping_rule_guard_rows BEFORE INSERT OR UPDATE OR DELETE ON "{S}".mapping_rule
                   FOR EACH ROW EXECUTE FUNCTION "{S}".mapping_rule_guard();""")

    op.execute(f"""
        CREATE FUNCTION "{S}".append_only() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION '% is append-only (% blocked)', TG_TABLE_NAME, TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
    """)
    for table in APPEND_ONLY:
        op.execute(f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON "{S}".{table}
                       FOR EACH ROW EXECUTE FUNCTION "{S}".append_only();""")


def downgrade() -> None:
    for table in APPEND_ONLY:
        op.execute(f'DROP TRIGGER IF EXISTS {table}_append_only ON "{S}".{table}')
    op.execute(f'DROP FUNCTION IF EXISTS "{S}".append_only()')
    op.execute(f'DROP TRIGGER IF EXISTS mapping_rule_guard_rows ON "{S}".mapping_rule')
    op.execute(f'DROP FUNCTION IF EXISTS "{S}".mapping_rule_guard()')
    op.execute(f'DROP TRIGGER IF EXISTS mapping_version_guard_truncate ON "{S}".mapping_version')
    op.execute(f'DROP TRIGGER IF EXISTS mapping_version_guard_rows ON "{S}".mapping_version')
    op.execute(f'DROP FUNCTION IF EXISTS "{S}".mapping_version_guard()')
    for table in ("field_lineage", "staged_relationship", "staged_record", "mapping_execution", "mapping_approval",
                  "mapping_validation_run", "mapping_rule", "mapping_version", "mapping_definition"):
        op.drop_table(table, schema=S)
