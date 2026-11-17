"""AI runtime: encrypted AI secrets and the append-only AI invocation log

Revision ID: 0008
Revises: 0007
Create Date: 2026-11-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"
J = postgresql.JSONB


def _base() -> list[sa.Column]:
    return [sa.Column("id", sa.Uuid(), nullable=False), sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "ai_secret", *_base(),
        sa.Column("name", sa.String(64), nullable=False),
        sa.Column("ciphertext", sa.Text(), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "name", name="uq_ai_secret_tenant_name"),
        schema=S,
    )
    op.create_index("ix_ai_secret_tenant_id", "ai_secret", ["tenant_id"], schema=S)
    op.create_table(
        "ai_invocation", *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=True),
        sa.Column("task", sa.String(40), nullable=False),
        sa.Column("profile_ref", sa.String(80), nullable=True),
        sa.Column("deployment_ref", sa.String(80), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("chain", J(), nullable=False),
        sa.Column("errors", J(), nullable=False),
        sa.Column("needs_human_review", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("request_hash", sa.String(71), nullable=False),
        sa.Column("output_hash", sa.String(71), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        schema=S,
    )
    op.create_index("ix_ai_invocation_tenant_id", "ai_invocation", ["tenant_id"], schema=S)
    op.create_index("ix_ai_invocation_run_id", "ai_invocation", ["run_id"], schema=S)
    op.execute(f"""CREATE TRIGGER ai_invocation_append_only BEFORE UPDATE OR DELETE ON "{S}".ai_invocation
                   FOR EACH ROW EXECUTE FUNCTION "{S}".append_only();""")


def downgrade() -> None:
    op.execute(f'DROP TRIGGER IF EXISTS ai_invocation_append_only ON "{S}".ai_invocation')
    op.drop_table("ai_invocation", schema=S)
    op.drop_table("ai_secret", schema=S)
