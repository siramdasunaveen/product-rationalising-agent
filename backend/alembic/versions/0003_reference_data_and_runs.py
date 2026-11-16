"""reference data (portfolio, market, product_family) and runs

Revision ID: 0003
Revises: 0002
Create Date: 2026-11-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"


def _base_columns() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.create_table(
        "portfolio",
        *_base_columns(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_portfolio"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_portfolio_tenant_code"),
        schema=S,
    )
    op.create_index("ix_portfolio_tenant_id", "portfolio", ["tenant_id"], schema=S)

    op.create_table(
        "market",
        *_base_columns(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("timezone", sa.String(64), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_market"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_market_tenant_code"),
        schema=S,
    )
    op.create_index("ix_market_tenant_id", "market", ["tenant_id"], schema=S)

    op.create_table(
        "product_family",
        *_base_columns(),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("parent_id", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_product_family"),
        sa.UniqueConstraint("tenant_id", "code", name="uq_product_family_tenant_code"),
        sa.ForeignKeyConstraint(["portfolio_id"], [f"{S}.portfolio.id"],
                                name="fk_product_family_portfolio_id_portfolio"),
        sa.ForeignKeyConstraint(["parent_id"], [f"{S}.product_family.id"],
                                name="fk_product_family_parent_id_product_family"),
        schema=S,
    )
    op.create_index("ix_product_family_tenant_id", "product_family", ["tenant_id"], schema=S)
    op.create_index("ix_product_family_portfolio_id", "product_family", ["portfolio_id"], schema=S)

    op.create_table(
        "run",
        *_base_columns(),
        sa.Column("run_code", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("state", sa.String(32), nullable=False),
        sa.Column("portfolio_id", sa.Uuid(), nullable=False),
        sa.Column("market_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("product_family_ids", postgresql.ARRAY(sa.Uuid()), nullable=False),
        sa.Column("period_start", sa.Date(), nullable=False),
        sa.Column("period_end", sa.Date(), nullable=False),
        sa.Column("objectives", postgresql.ARRAY(sa.String(40)), nullable=False),
        sa.Column("owner_id", sa.String(128), nullable=False),
        sa.Column("owner_name", sa.String(120), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_run"),
        sa.UniqueConstraint("tenant_id", "run_code", name="uq_run_tenant_code"),
        sa.ForeignKeyConstraint(["portfolio_id"], [f"{S}.portfolio.id"], name="fk_run_portfolio_id_portfolio"),
        schema=S,
    )
    op.create_index("ix_run_tenant_id", "run", ["tenant_id"], schema=S)
    op.execute(
        f"CREATE UNIQUE INDEX uq_run_tenant_active_name ON {S}.run (tenant_id, lower(name)) "
        "WHERE state <> 'CLOSED'"
    )


def downgrade() -> None:
    op.drop_table("run", schema=S)
    op.drop_table("product_family", schema=S)
    op.drop_table("market", schema=S)
    op.drop_table("portfolio", schema=S)
