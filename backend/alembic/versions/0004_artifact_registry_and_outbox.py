"""versioned artifact registry (artifact, artifact_version) and transactional outbox

Immutability is enforced here as well as in the service:
- a new version must start as DRAFT;
- once a version is not DRAFT, its content, digest, validity and identity cannot change;
- status changes must follow the lifecycle (DRAFT -> PUBLISHED -> DEPRECATED/SUPERSEDED/RETIRED);
- only DRAFT versions can be deleted; TRUNCATE is blocked;
- an artifact's kind and key never change.

Revision ID: 0004
Revises: 0003
Create Date: 2026-11-16
"""
from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None

S = "rationalisation"

# Keep in sync with app.domain.artifacts.TRANSITIONS (tests/test_artifacts_db.py checks every pair).
TRANSITIONS = [
    ("DRAFT", "PUBLISHED"),
    ("PUBLISHED", "DEPRECATED"), ("PUBLISHED", "SUPERSEDED"), ("PUBLISHED", "RETIRED"),
    ("DEPRECATED", "SUPERSEDED"), ("DEPRECATED", "RETIRED"),
    ("SUPERSEDED", "RETIRED"),
]
STATUSES = ("DRAFT", "PUBLISHED", "DEPRECATED", "SUPERSEDED", "RETIRED")


def upgrade() -> None:
    op.create_table(
        "artifact",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("key", sa.String(64), nullable=False),
        sa.Column("name", sa.String(120), nullable=False),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_artifact"),
        sa.UniqueConstraint("tenant_id", "key", name="uq_artifact_tenant_key"),
        schema=S,
    )
    op.create_index("ix_artifact_tenant_id", "artifact", ["tenant_id"], schema=S)

    status_list = ", ".join(f"'{s}'" for s in STATUSES)
    op.create_table(
        "artifact_version",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("artifact_id", sa.Uuid(), nullable=False),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("version_major", sa.Integer(), nullable=False),
        sa.Column("version_minor", sa.Integer(), nullable=False),
        sa.Column("parent_version_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("content", postgresql.JSONB(), nullable=False),
        sa.Column("digest", sa.String(71), nullable=False),
        sa.Column("valid_from", sa.Date(), nullable=True),
        sa.Column("valid_to", sa.Date(), nullable=True),
        sa.Column("change_note", sa.Text(), nullable=True),
        sa.Column("created_by", sa.String(128), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("published_by", sa.String(128), nullable=True),
        sa.Column("status_changed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("status_changed_by", sa.String(128), nullable=True),
        sa.Column("status_reason", sa.Text(), nullable=True),
        sa.Column("lock_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("id", name="pk_artifact_version"),
        sa.UniqueConstraint("artifact_id", "version_major", "version_minor", name="uq_artifact_version_number"),
        sa.ForeignKeyConstraint(["artifact_id"], [f"{S}.artifact.id"],
                                name="fk_artifact_version_artifact_id_artifact"),
        sa.ForeignKeyConstraint(["parent_version_id"], [f"{S}.artifact_version.id"],
                                name="fk_artifact_version_parent_version_id_artifact_version"),
        sa.CheckConstraint(f"status IN ({status_list})", name="ck_artifact_version_status"),
        sa.CheckConstraint("digest ~ '^sha256:[0-9a-f]{64}$'", name="ck_artifact_version_digest"),
        sa.CheckConstraint("version_major >= 0 AND version_minor >= 0", name="ck_artifact_version_number"),
        sa.CheckConstraint("valid_from IS NULL OR valid_to IS NULL OR valid_from <= valid_to",
                           name="ck_artifact_version_validity"),
        sa.CheckConstraint("jsonb_typeof(content) = 'object'", name="ck_artifact_version_content_object"),
        schema=S,
    )
    op.create_index("ix_artifact_version_tenant_id", "artifact_version", ["tenant_id"], schema=S)
    op.create_index("ix_artifact_version_artifact_id", "artifact_version", ["artifact_id"], schema=S)
    op.execute(f"CREATE UNIQUE INDEX uq_artifact_version_published ON {S}.artifact_version (artifact_id) "
               "WHERE status = 'PUBLISHED'")
    op.execute(f"CREATE UNIQUE INDEX uq_artifact_version_draft ON {S}.artifact_version (artifact_id) "
               "WHERE status = 'DRAFT'")

    allowed = " OR ".join(f"(OLD.status = '{a}' AND NEW.status = '{b}')" for a, b in TRANSITIONS)
    op.execute(f"""
        CREATE FUNCTION "{S}".artifact_version_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF TG_OP = 'TRUNCATE' THEN
                RAISE EXCEPTION 'artifact_version cannot be truncated' USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF TG_OP = 'INSERT' THEN
                IF NEW.status <> 'DRAFT' THEN
                    RAISE EXCEPTION 'a new artifact version must start as DRAFT (got %)', NEW.status
                        USING ERRCODE = 'check_violation';
                END IF;
                RETURN NEW;
            END IF;
            IF TG_OP = 'DELETE' THEN
                IF OLD.status <> 'DRAFT' THEN
                    RAISE EXCEPTION 'artifact version %.% is % and cannot be deleted',
                        OLD.version_major, OLD.version_minor, OLD.status USING ERRCODE = 'insufficient_privilege';
                END IF;
                RETURN OLD;
            END IF;
            -- UPDATE
            IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.artifact_id <> OLD.artifact_id
               OR NEW.kind <> OLD.kind OR NEW.version_major <> OLD.version_major
               OR NEW.version_minor <> OLD.version_minor
               OR NEW.parent_version_id IS DISTINCT FROM OLD.parent_version_id
               OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'the identity of an artifact version cannot change'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF OLD.status <> 'DRAFT' AND (
                   NEW.content IS DISTINCT FROM OLD.content OR NEW.digest <> OLD.digest
                   OR NEW.valid_from IS DISTINCT FROM OLD.valid_from OR NEW.valid_to IS DISTINCT FROM OLD.valid_to
                   OR NEW.change_note IS DISTINCT FROM OLD.change_note
                   OR NEW.published_at IS DISTINCT FROM OLD.published_at
                   OR NEW.published_by IS DISTINCT FROM OLD.published_by) THEN
                RAISE EXCEPTION 'artifact version %.% is % and immutable; create a new version',
                    OLD.version_major, OLD.version_minor, OLD.status USING ERRCODE = 'insufficient_privilege';
            END IF;
            IF NEW.status <> OLD.status AND NOT ({allowed}) THEN
                RAISE EXCEPTION 'artifact version status cannot change from % to %', OLD.status, NEW.status
                    USING ERRCODE = 'check_violation';
            END IF;
            IF NEW.status = 'PUBLISHED' AND OLD.status = 'DRAFT'
               AND (NEW.published_at IS NULL OR NEW.published_by IS NULL) THEN
                RAISE EXCEPTION 'publishing needs published_at and published_by' USING ERRCODE = 'check_violation';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute(f"""
        CREATE TRIGGER artifact_version_guard_rows
        BEFORE INSERT OR UPDATE OR DELETE ON "{S}".artifact_version
        FOR EACH ROW EXECUTE FUNCTION "{S}".artifact_version_guard();
    """)
    op.execute(f"""
        CREATE TRIGGER artifact_version_guard_truncate
        BEFORE TRUNCATE ON "{S}".artifact_version
        FOR EACH STATEMENT EXECUTE FUNCTION "{S}".artifact_version_guard();
    """)

    op.execute(f"""
        CREATE FUNCTION "{S}".artifact_guard() RETURNS trigger
        LANGUAGE plpgsql AS $$
        BEGIN
            IF NEW.id <> OLD.id OR NEW.tenant_id <> OLD.tenant_id OR NEW.kind <> OLD.kind
               OR NEW.key <> OLD.key OR NEW.created_by <> OLD.created_by OR NEW.created_at <> OLD.created_at THEN
                RAISE EXCEPTION 'the kind and key of an artifact cannot change'
                    USING ERRCODE = 'insufficient_privilege';
            END IF;
            RETURN NEW;
        END;
        $$;
    """)
    op.execute(f"""
        CREATE TRIGGER artifact_guard_update
        BEFORE UPDATE ON "{S}".artifact
        FOR EACH ROW EXECUTE FUNCTION "{S}".artifact_guard();
    """)

    op.create_table(
        "outbox_event",
        sa.Column("id", sa.BigInteger(), autoincrement=True, nullable=False),
        sa.Column("tenant_id", sa.String(64), nullable=False),
        sa.Column("event_id", sa.Uuid(), nullable=False),
        sa.Column("event_type", sa.String(64), nullable=False),
        sa.Column("aggregate_type", sa.String(64), nullable=False),
        sa.Column("aggregate_id", sa.String(128), nullable=False),
        sa.Column("payload", postgresql.JSONB(), nullable=False),
        sa.Column("occurred_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("attempts", sa.Integer(), nullable=False),
        sa.Column("last_error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_outbox_event"),
        sa.UniqueConstraint("event_id", name="uq_outbox_event_event_id"),
        schema=S,
    )
    op.create_index("ix_outbox_event_tenant_id", "outbox_event", ["tenant_id"], schema=S)
    op.execute(f"CREATE INDEX ix_outbox_event_pending ON {S}.outbox_event (id) WHERE published_at IS NULL")


def downgrade() -> None:
    op.drop_table("outbox_event", schema=S)
    op.execute(f'DROP TRIGGER IF EXISTS artifact_guard_update ON "{S}".artifact')
    op.execute(f'DROP FUNCTION IF EXISTS "{S}".artifact_guard()')
    op.execute(f'DROP TRIGGER IF EXISTS artifact_version_guard_truncate ON "{S}".artifact_version')
    op.execute(f'DROP TRIGGER IF EXISTS artifact_version_guard_rows ON "{S}".artifact_version')
    op.execute(f'DROP FUNCTION IF EXISTS "{S}".artifact_version_guard()')
    op.drop_table("artifact_version", schema=S)
    op.drop_table("artifact", schema=S)
