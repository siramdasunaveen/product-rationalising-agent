"""Initial: schema rationalisation + append-only audit_event

Revision ID: 0001
Revises:
Create Date: 2026-11-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

SCHEMA = "rationalisation"


def upgrade() -> None:
    op.execute(f'CREATE SCHEMA IF NOT EXISTS "{SCHEMA}"')

    op.create_table(
        "audit_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("seq", sa.BigInteger(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("actor_roles", postgresql.JSONB(), nullable=False),
        sa.Column("auth_context", postgresql.JSONB(), nullable=False),
        sa.Column("action", sa.String(128), nullable=False),
        sa.Column("entity_type", sa.String(64), nullable=False),
        sa.Column("entity_id", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("prev_hash", sa.String(64), nullable=False),
        sa.Column("hash", sa.String(64), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_audit_event"),
        sa.UniqueConstraint("tenant_id", "seq", name="uq_audit_event_tenant_seq"),
        schema=SCHEMA,
    )
    op.create_index("ix_audit_event_tenant_id", "audit_event", ["tenant_id"], schema=SCHEMA)
    op.create_index("ix_audit_event_tenant_entity", "audit_event",
                    ["tenant_id", "entity_type", "entity_id"], schema=SCHEMA)

    # Append-only: block UPDATE, DELETE and TRUNCATE at the database level.
    op.execute(f"""
        CREATE FUNCTION "{SCHEMA}".audit_event_block_changes() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'audit_event is append-only (% blocked)', TG_OP
                USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
    """)
    op.execute(f"""
        CREATE TRIGGER audit_event_no_update_delete
        BEFORE UPDATE OR DELETE ON "{SCHEMA}".audit_event
        FOR EACH ROW EXECUTE FUNCTION "{SCHEMA}".audit_event_block_changes();
    """)
    op.execute(f"""
        CREATE TRIGGER audit_event_no_truncate
        BEFORE TRUNCATE ON "{SCHEMA}".audit_event
        FOR EACH STATEMENT EXECUTE FUNCTION "{SCHEMA}".audit_event_block_changes();
    """)


def downgrade() -> None:
    op.execute(f'DROP TABLE IF EXISTS "{SCHEMA}".audit_event')
    op.execute(f'DROP FUNCTION IF EXISTS "{SCHEMA}".audit_event_block_changes()')
