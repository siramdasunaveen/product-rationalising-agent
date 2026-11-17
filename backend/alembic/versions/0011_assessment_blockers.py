"""assessment: candidate score/dimensions/recommendation/priority/status, blocker records, run projection version

Revision ID: 0011
Revises: 0010
Create Date: 2026-11-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0011"
down_revision: str | None = "0010"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"
J = postgresql.JSONB

CANDIDATE_COLUMNS = [
    sa.Column("similarity", sa.Integer(), nullable=True),
    sa.Column("opportunity_score", sa.Integer(), nullable=True),
    sa.Column("score_inputs", J(), nullable=True),
    sa.Column("score_coverage", sa.Integer(), nullable=True),
    sa.Column("dimensions", J(), nullable=True),
    sa.Column("guardrail", sa.String(20), nullable=True),
    sa.Column("recommendation", J(), nullable=True),
    sa.Column("impact", J(), nullable=True),
    sa.Column("priority_index", sa.Integer(), nullable=True),
    sa.Column("base_priority", sa.String(10), nullable=True),
    sa.Column("effective_priority", sa.String(10), nullable=True),
    sa.Column("priority_detail", J(), nullable=True),
    sa.Column("candidate_status", sa.String(16), nullable=True),
    sa.Column("hidden", sa.Boolean(), nullable=False, server_default=sa.false()),
    sa.Column("assessment_hash", sa.String(71), nullable=True),
]


def upgrade() -> None:
    for col in CANDIDATE_COLUMNS:
        op.add_column("candidate", col, schema=S)
    op.create_index("ix_candidate_effective_priority", "candidate", ["run_id", "effective_priority"], schema=S)
    op.add_column("run", sa.Column("projection_version", sa.Integer(), nullable=False, server_default="0"), schema=S)
    op.create_table(
        "blocker",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("candidate_code", sa.String(16), nullable=True),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("dedupe_key", sa.String(300), nullable=False),
        sa.Column("rule", sa.String(40), nullable=False),
        sa.Column("category", sa.String(16), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("consequence", sa.String(16), nullable=False),
        sa.Column("severity", sa.String(10), nullable=False),
        sa.Column("domain", sa.String(16), nullable=False),
        sa.Column("subject", sa.String(255), nullable=False),
        sa.Column("message", sa.Text(), nullable=False),
        sa.Column("owner_role", sa.String(40), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("applicable", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("superseded", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("facts", J(), nullable=False),
        sa.Column("resolved_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("resolved_by", sa.String(128), nullable=True),
        sa.Column("resolution", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_blocker_run_id_run"),
        sa.ForeignKeyConstraint(["analysis_id"], [f"{S}.analysis_run.id"], name="fk_blocker_analysis_id_analysis_run"),
        sa.UniqueConstraint("run_id", "code", name="uq_blocker_run_code"),
        schema=S,
    )
    op.create_index("ix_blocker_tenant_id", "blocker", ["tenant_id"], schema=S)
    op.create_index("ix_blocker_run_state", "blocker", ["run_id", "state"], schema=S)
    op.create_index("ix_blocker_candidate_id", "blocker", ["candidate_id"], schema=S)


def downgrade() -> None:
    op.drop_table("blocker", schema=S)
    op.drop_column("run", "projection_version", schema=S)
    op.drop_index("ix_candidate_effective_priority", table_name="candidate", schema=S)
    for col in reversed(CANDIDATE_COLUMNS):
        op.drop_column("candidate", col.name, schema=S)
