"""analysis workflow: analysis runs with stages, detector findings, normalized candidates

Revision ID: 0010
Revises: 0009
Create Date: 2026-11-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0010"
down_revision: str | None = "0009"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"
J = postgresql.JSONB


def _base() -> list[sa.Column]:
    return [sa.Column("id", sa.Uuid(), nullable=False), sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "analysis_run", *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(40), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("snapshot_id", sa.Uuid(), nullable=False),
        sa.Column("manifest_hash", sa.String(71), nullable=False),
        sa.Column("stages", J(), nullable=False),
        sa.Column("source_products", J(), nullable=True),
        sa.Column("counts", J(), nullable=True),
        sa.Column("detector_summary", J(), nullable=True),
        sa.Column("result_hash", sa.String(71), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.Column("finished_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_analysis_run_run_id_run"),
        sa.ForeignKeyConstraint(["snapshot_id"], [f"{S}.evidence_snapshot.id"],
                                name="fk_analysis_run_snapshot_id_evidence_snapshot"),
        sa.UniqueConstraint("run_id", "seq", name="uq_analysis_run_seq"),
        schema=S,
    )
    op.create_index("ix_analysis_run_tenant_id", "analysis_run", ["tenant_id"], schema=S)
    op.create_index("ix_analysis_run_run_id", "analysis_run", ["run_id"], schema=S)
    op.create_table(
        "detector_finding", *_base(),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("finding_key", sa.String(32), nullable=False),
        sa.Column("detector_type", sa.String(32), nullable=False),
        sa.Column("detector_version", sa.String(16), nullable=False),
        sa.Column("subjects", J(), nullable=False),
        sa.Column("signal", sa.Integer(), nullable=False),
        sa.Column("threshold", sa.Integer(), nullable=False),
        sa.Column("band", sa.String(16), nullable=False),
        sa.Column("factors", J(), nullable=False),
        sa.Column("coverage", sa.Integer(), nullable=False),
        sa.Column("evidence_ids", J(), nullable=False),
        sa.Column("notes", J(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("candidate_id", sa.Uuid(), nullable=True),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["analysis_id"], [f"{S}.analysis_run.id"],
                                name="fk_detector_finding_analysis_id_analysis_run"),
        schema=S,
    )
    for col in ("tenant_id", "analysis_id", "run_id"):
        op.create_index(f"ix_detector_finding_{col}", "detector_finding", [col], schema=S)
    op.create_table(
        "candidate", *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("analysis_id", sa.Uuid(), nullable=False),
        sa.Column("code", sa.String(16), nullable=False),
        sa.Column("group_key", sa.String(71), nullable=False),
        sa.Column("subjects", J(), nullable=False),
        sa.Column("primary_type", sa.String(32), nullable=False),
        sa.Column("supporting_types", J(), nullable=False),
        sa.Column("max_signal", sa.Integer(), nullable=False),
        sa.Column("finding_count", sa.Integer(), nullable=False),
        sa.Column("evidence_ids", J(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("superseded_by", sa.Uuid(), nullable=True),
        sa.Column("lineage", J(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_candidate_run_id_run"),
        sa.ForeignKeyConstraint(["analysis_id"], [f"{S}.analysis_run.id"],
                                name="fk_candidate_analysis_id_analysis_run"),
        sa.UniqueConstraint("analysis_id", "group_key", name="uq_candidate_analysis_group"),
        schema=S,
    )
    for col in ("tenant_id", "run_id", "analysis_id"):
        op.create_index(f"ix_candidate_{col}", "candidate", [col], schema=S)
    # Findings are facts of an analysis: append-only.
    op.execute(f"""CREATE TRIGGER detector_finding_append_only BEFORE UPDATE OR DELETE ON "{S}".detector_finding
                   FOR EACH ROW EXECUTE FUNCTION "{S}".append_only();""")


def downgrade() -> None:
    op.execute(f'DROP TRIGGER IF EXISTS detector_finding_append_only ON "{S}".detector_finding')
    op.drop_table("candidate", schema=S)
    op.drop_table("detector_finding", schema=S)
    op.drop_table("analysis_run", schema=S)
