"""live connectors: approved source systems with encrypted credentials, pull lineage

Revision ID: 0017
Revises: 0016
Create Date: 2026-11-17
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0017"
down_revision: str | None = "0016"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"


def _base() -> list[sa.Column]:
    return [sa.Column("id", sa.Uuid(), nullable=False), sa.Column("tenant_id", sa.String(64), nullable=False),
            sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False)]


def upgrade() -> None:
    op.create_table(
        "connector", *_base(),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("key", sa.String(40), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("system", sa.String(40), nullable=False),
        sa.Column("config", postgresql.JSONB(), nullable=False),
        sa.Column("client_id", sa.String(255), nullable=True),
        sa.Column("secret_ciphertext", sa.Text(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="ACTIVE"),
        sa.Column("last_check", postgresql.JSONB(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_connector_tenant_key"),
        schema=S,
    )
    op.create_table(
        "connector_pull", *_base(),
        sa.Column("connector_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("upload_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("rows", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("soql", sa.Text(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("requested_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(["connector_id"], [f"{S}.connector.id"], name="fk_connector_pull_connector_id_connector"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_connector_pull_run_id_run"),
        schema=S,
    )
    op.create_index("ix_connector_pull_run", "connector_pull", ["tenant_id", "run_id", "domain"], schema=S)


def downgrade() -> None:
    op.drop_table("connector_pull", schema=S)
    op.drop_table("connector", schema=S)
