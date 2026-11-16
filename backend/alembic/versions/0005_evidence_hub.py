"""evidence hub: run.evidence_domains, evidence_source, source_schema_version (immutable), evidence_upload

Revision ID: 0005
Revises: 0004
Create Date: 2026-11-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"


def _base() -> list[sa.Column]:
    return [
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
    ]


def upgrade() -> None:
    op.add_column("run", sa.Column("evidence_domains", postgresql.ARRAY(sa.String(40)), nullable=True), schema=S)

    op.create_table(
        "evidence_source",
        *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("source_type", sa.String(32), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_source"),
        sa.UniqueConstraint("run_id", "domain", "source_type", name="uq_evidence_source_run_domain_type"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_evidence_source_run_id_run"),
        schema=S,
    )
    op.create_index("ix_evidence_source_tenant_id", "evidence_source", ["tenant_id"], schema=S)
    op.create_index("ix_evidence_source_run_id", "evidence_source", ["run_id"], schema=S)

    op.create_table(
        "source_schema_version",
        *_base(),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("fingerprint", sa.String(71), nullable=False),
        sa.Column("sheet_name", sa.String(64), nullable=False),
        sa.Column("header_row", sa.Integer(), nullable=False),
        sa.Column("columns", postgresql.JSONB(), nullable=False),
        sa.Column("template_ref", sa.String(80), nullable=True),
        sa.Column("template_match", postgresql.JSONB(), nullable=True),
        sa.Column("first_upload_id", sa.Uuid(), nullable=False),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_source_schema_version"),
        sa.UniqueConstraint("source_id", "version", name="uq_source_schema_version_number"),
        sa.UniqueConstraint("source_id", "fingerprint", name="uq_source_schema_version_fingerprint"),
        sa.ForeignKeyConstraint(["source_id"], [f"{S}.evidence_source.id"],
                                name="fk_source_schema_version_source_id_evidence_source"),
        sa.CheckConstraint("fingerprint ~ '^sha256:[0-9a-f]{64}$'", name="ck_source_schema_version_fingerprint"),
        schema=S,
    )
    op.create_index("ix_source_schema_version_tenant_id", "source_schema_version", ["tenant_id"], schema=S)
    op.create_index("ix_source_schema_version_source_id", "source_schema_version", ["source_id"], schema=S)
    op.execute(f"""
        CREATE FUNCTION "{S}".source_schema_version_block_changes() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            RAISE EXCEPTION 'source_schema_version is immutable (% blocked); a new structure is a new version',
                TG_OP USING ERRCODE = 'insufficient_privilege';
        END;
        $$;
    """)
    op.execute(f"""
        CREATE TRIGGER source_schema_version_immutable
        BEFORE UPDATE OR DELETE ON "{S}".source_schema_version
        FOR EACH ROW EXECUTE FUNCTION "{S}".source_schema_version_block_changes();
    """)
    op.execute(f"""
        CREATE TRIGGER source_schema_version_no_truncate
        BEFORE TRUNCATE ON "{S}".source_schema_version
        FOR EACH STATEMENT EXECUTE FUNCTION "{S}".source_schema_version_block_changes();
    """)

    op.create_table(
        "evidence_upload",
        *_base(),
        sa.Column("run_id", sa.Uuid(), nullable=False),
        sa.Column("source_id", sa.Uuid(), nullable=False),
        sa.Column("domain", sa.String(40), nullable=False),
        sa.Column("filename", sa.String(255), nullable=False),
        sa.Column("size_bytes", sa.BigInteger(), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("storage_key", sa.String(512), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("reasons", postgresql.JSONB(), nullable=False),
        sa.Column("scan", postgresql.JSONB(), nullable=True),
        sa.Column("discovery", postgresql.JSONB(), nullable=True),
        sa.Column("job_id", sa.String(64), nullable=True),
        sa.Column("uploaded_by", sa.String(128), nullable=False),
        sa.Column("uploaded_by_name", sa.String(120), nullable=False),
        sa.Column("processed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("schema_version_id", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by", sa.String(128), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_evidence_upload"),
        sa.ForeignKeyConstraint(["run_id"], [f"{S}.run.id"], name="fk_evidence_upload_run_id_run"),
        sa.ForeignKeyConstraint(["source_id"], [f"{S}.evidence_source.id"],
                                name="fk_evidence_upload_source_id_evidence_source"),
        sa.ForeignKeyConstraint(["schema_version_id"], [f"{S}.source_schema_version.id"],
                                name="fk_evidence_upload_schema_version_id_source_schema_version"),
        sa.CheckConstraint("status IN ('PROCESSING', 'READY', 'REJECTED')", name="ck_evidence_upload_status"),
        schema=S,
    )
    op.create_index("ix_evidence_upload_tenant_id", "evidence_upload", ["tenant_id"], schema=S)
    op.create_index("ix_evidence_upload_run_id", "evidence_upload", ["run_id"], schema=S)
    op.create_index("ix_evidence_upload_source_id", "evidence_upload", ["source_id"], schema=S)
    op.execute(f"CREATE UNIQUE INDEX uq_evidence_upload_source_sha ON {S}.evidence_upload (source_id, sha256) "
               "WHERE status <> 'REJECTED'")


def downgrade() -> None:
    op.drop_table("evidence_upload", schema=S)
    op.execute(f'DROP TRIGGER IF EXISTS source_schema_version_no_truncate ON "{S}".source_schema_version')
    op.execute(f'DROP TRIGGER IF EXISTS source_schema_version_immutable ON "{S}".source_schema_version')
    op.execute(f'DROP FUNCTION IF EXISTS "{S}".source_schema_version_block_changes()')
    op.drop_table("source_schema_version", schema=S)
    op.drop_table("evidence_source", schema=S)
    op.drop_column("run", "evidence_domains", schema=S)
