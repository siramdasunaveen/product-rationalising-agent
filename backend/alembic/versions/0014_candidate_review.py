"""candidate review: discussion comments and recommendation explanations (append-only), personal saved views

Revision ID: 0014
Revises: 0011
Create Date: 2026-11-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0014"
down_revision: str | None = "0011"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"


def _base() -> list[sa.Column]:
    return [sa.Column("id", sa.Uuid(), nullable=False), sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "candidate_comment", *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_code", sa.String(16), nullable=False),
        sa.Column("author_id", sa.String(128), nullable=False),
        sa.Column("body", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_candidate_comment_run_id_run"),
        schema=S,
    )
    op.create_index("ix_candidate_comment_lookup", "candidate_comment", ["tenant_id", "run_id", "candidate_code"],
                    schema=S)
    op.create_table(
        "candidate_explanation", *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_code", sa.String(16), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("deployment_ref", sa.String(80), nullable=True),
        sa.Column("model", sa.String(120), nullable=True),
        sa.Column("sentences", postgresql.JSONB(), nullable=False),
        sa.Column("confidence", sa.Integer(), nullable=True),
        sa.Column("below_threshold", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_candidate_explanation_run_id_run"),
        schema=S,
    )
    op.create_index("ix_candidate_explanation_lookup", "candidate_explanation",
                    ["tenant_id", "run_id", "candidate_code"], schema=S)
    op.create_table(
        "saved_view", *_base(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("name", sa.String(80), nullable=False),
        sa.Column("scope", sa.String(16), nullable=False),
        sa.Column("filters", postgresql.JSONB(), nullable=False),
        sa.Column("sort", postgresql.JSONB(), nullable=False),
        sa.Column("visible_columns", postgresql.JSONB(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "owner_id", "name", name="uq_saved_view_owner_name"),
        schema=S,
    )
    op.create_index("ix_saved_view_owner", "saved_view", ["tenant_id", "owner_id"], schema=S)
    for table in ("candidate_comment", "candidate_explanation"):
        op.execute(f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON "{S}".{table}
                       FOR EACH ROW EXECUTE FUNCTION "{S}".append_only();""")


def downgrade() -> None:
    op.drop_table("saved_view", schema=S)
    for table in ("candidate_explanation", "candidate_comment"):
        op.execute(f'DROP TRIGGER IF EXISTS {table}_append_only ON "{S}".{table}')
        op.drop_table(table, schema=S)
