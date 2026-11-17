"""decisions and approvals: decision aggregate, immutable versions, approval tasks and append-only actions

Revision ID: 0016
Revises: 0015
Create Date: 2026-11-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0016"
down_revision: str | None = "0015"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"


def _base() -> list[sa.Column]:
    return [sa.Column("id", sa.Uuid(), nullable=False), sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "decision", *_base(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("candidate_code", sa.String(16), nullable=False),
        sa.Column("current_version_id", sa.Uuid(), nullable=True),
        sa.Column("current_version_no", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("approved_version_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_decision_run_id_run"),
        sa.UniqueConstraint("run_id", "code", name="uq_decision_run_code"),
        sa.UniqueConstraint("run_id", "candidate_code", name="uq_decision_run_candidate"),
        schema=S,
    )
    op.create_index("ix_decision_run", "decision", ["tenant_id", "run_id", "state"], schema=S)
    op.create_table(
        "decision_version", *_base(),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("version_no", sa.Integer(), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("snapshot_code", sa.String(16), nullable=False),
        sa.Column("manifest_hash", sa.String(71), nullable=False),
        sa.Column("recommendation", postgresql.JSONB(), nullable=False),
        sa.Column("disposition", sa.String(16), nullable=False),
        sa.Column("target_product_id", sa.String(64), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("rationale", sa.Text(), nullable=False),
        sa.Column("commercial_treatment", sa.String(200), nullable=True),
        sa.Column("conditions", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("readiness", postgresql.JSONB(), nullable=True),
        sa.Column("route", postgresql.JSONB(), nullable=True),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("reason", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["decision_id"], [f"{S}.decision.id"], name="fk_decision_version_decision_id_decision"),
        sa.UniqueConstraint("decision_id", "version_no", name="uq_decision_version_no"),
        schema=S,
    )
    op.create_table(
        "approval_task", *_base(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("scope", sa.String(40), nullable=False),
        sa.Column("state", sa.String(16), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("acted_by", sa.String(128), nullable=True),
        sa.Column("acted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_approval_task_run_id_run"),
        sa.ForeignKeyConstraint(["decision_id"], [f"{S}.decision.id"], name="fk_approval_task_decision_id_decision"),
        sa.ForeignKeyConstraint(["version_id"], [f"{S}.decision_version.id"],
                                name="fk_approval_task_version_id_decision_version"),
        schema=S,
    )
    op.create_index("ix_approval_task_queue", "approval_task", ["tenant_id", "state", "scope"], schema=S)
    op.create_table(
        "approval_action", *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("decision_id", sa.Uuid(), nullable=False),
        sa.Column("version_id", sa.Uuid(), nullable=False),
        sa.Column("task_id", sa.Uuid(), nullable=True),
        sa.Column("scope", sa.String(40), nullable=True),
        sa.Column("action", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("actor_roles", postgresql.JSONB(), nullable=False, server_default="[]"),
        sa.Column("auth_context", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("content_hash", sa.String(71), nullable=False),
        sa.Column("policy_result", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.Column("comment", sa.Text(), nullable=True),
        sa.Column("resulting_state", sa.String(16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_approval_action_run_id_run"),
        sa.ForeignKeyConstraint(["decision_id"], [f"{S}.decision.id"], name="fk_approval_action_decision_id_decision"),
        sa.ForeignKeyConstraint(["version_id"], [f"{S}.decision_version.id"],
                                name="fk_approval_action_version_id_decision_version"),
        schema=S,
    )
    op.create_index("ix_approval_action_decision", "approval_action", ["decision_id", "created_at"], schema=S)
    for table in ("decision_version", "approval_action"):
        op.execute(f"""CREATE TRIGGER {table}_append_only BEFORE UPDATE OR DELETE ON "{S}".{table}
                       FOR EACH ROW EXECUTE FUNCTION "{S}".append_only();""")


def downgrade() -> None:
    for table in ("approval_action", "decision_version"):
        op.execute(f'DROP TRIGGER IF EXISTS {table}_append_only ON "{S}".{table}')
    op.drop_table("approval_action", schema=S)
    op.drop_table("approval_task", schema=S)
    op.drop_table("decision_version", schema=S)
    op.drop_table("decision", schema=S)
