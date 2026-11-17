"""evidence requests: logical evidence contracts per gap, lifecycle events (append-only)

Revision ID: 0015
Revises: 0014
Create Date: 2026-11-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0015"
down_revision: str | None = "0014"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"


def upgrade() -> None:
    op.create_table(
        "evidence_request",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("candidate_code", sa.String(16), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.Column("blocker_code", sa.String(16), nullable=True),
        sa.Column("blocker_id", sa.Uuid(), nullable=True),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("action", sa.String(16), nullable=True),
        sa.Column("trigger", sa.String(200), nullable=False),
        sa.Column("evidence_needed", sa.String(200), nullable=False),
        sa.Column("decision_impact", sa.String(200), nullable=False),
        sa.Column("consequence", sa.String(16), nullable=False),
        sa.Column("priority", sa.String(10), nullable=False),
        sa.Column("contract", postgresql.JSONB(), nullable=False),
        sa.Column("owner_role", sa.String(40), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=True),
        sa.Column("due_date", sa.Date(), nullable=True),
        sa.Column("fulfilment_method", sa.String(24), nullable=True),
        sa.Column("fulfilment_job", postgresql.JSONB(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("baseline_executions", postgresql.JSONB(), nullable=False),
        sa.Column("received_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("accepted_snapshot_id", sa.Uuid(), nullable=True),
        sa.Column("reassessment_analysis_id", sa.Uuid(), nullable=True),
        sa.Column("reassessment_status", sa.String(16), nullable=False, server_default="NONE"),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_evidence_request_run_id_run"),
        sa.ForeignKeyConstraint(["snapshot_id"], [f"{S}.evidence_snapshot.id"],
                                name="fk_evidence_request_snapshot_id_evidence_snapshot"),
        sa.UniqueConstraint("run_id", "code", name="uq_evidence_request_run_code"),
        schema=S,
    )
    op.create_index("ix_evidence_request_run", "evidence_request", ["tenant_id", "run_id", "status"], schema=S)
    op.create_index("ix_evidence_request_candidate", "evidence_request", ["tenant_id", "run_id", "candidate_code"],
                    schema=S)
    op.create_table(
        "evidence_request_event",
        sa.Column("id", sa.Uuid(), nullable=False), sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("request_id", sa.Uuid(), nullable=False),
        sa.Column("from_status", sa.String(16), nullable=True),
        sa.Column("to_status", sa.String(16), nullable=False),
        sa.Column("actor_id", sa.String(128), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("detail", postgresql.JSONB(), nullable=False, server_default="{}"),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["request_id"], [f"{S}.evidence_request.id"],
                                name="fk_evidence_request_event_request_id_evidence_request"),
        schema=S,
    )
    op.create_index("ix_evidence_request_event_request", "evidence_request_event", ["request_id", "created_at"],
                    schema=S)
    op.execute(f"""CREATE TRIGGER evidence_request_event_append_only BEFORE UPDATE OR DELETE
                   ON "{S}".evidence_request_event FOR EACH ROW EXECUTE FUNCTION "{S}".append_only();""")


def downgrade() -> None:
    op.execute(f'DROP TRIGGER IF EXISTS evidence_request_event_append_only ON "{S}".evidence_request_event')
    op.drop_table("evidence_request_event", schema=S)
    op.drop_table("evidence_request", schema=S)
