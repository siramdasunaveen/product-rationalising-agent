"""run configuration (Scoring & AI Route draft), start fields and the immutable run manifest

Revision ID: 0009
Revises: 0008
Create Date: 2026-11-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"


def upgrade() -> None:
    op.add_column("run", sa.Column("configuration", postgresql.JSONB(), nullable=True), schema=S)
    op.add_column("run", sa.Column("started_at", sa.DateTime(timezone=True), nullable=True), schema=S)
    op.add_column("run", sa.Column("started_by", sa.String(128), nullable=True), schema=S)
    op.create_table(
        "run_manifest",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("manifest", postgresql.JSONB(), nullable=False),
        sa.Column("manifest_hash", sa.String(71), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_run_manifest_run_id_run"),
        sa.UniqueConstraint("run_id", name="uq_run_manifest_run"),
        schema=S,
    )
    op.create_index("ix_run_manifest_tenant_id", "run_manifest", ["tenant_id"], schema=S)
    op.execute(f"""CREATE TRIGGER run_manifest_append_only BEFORE UPDATE OR DELETE ON "{S}".run_manifest
                   FOR EACH ROW EXECUTE FUNCTION "{S}".append_only();""")


def downgrade() -> None:
    op.execute(f'DROP TRIGGER IF EXISTS run_manifest_append_only ON "{S}".run_manifest')
    op.drop_table("run_manifest", schema=S)
    op.drop_column("run", "started_by", schema=S)
    op.drop_column("run", "started_at", schema=S)
    op.drop_column("run", "configuration", schema=S)
