"""SQLAlchemy models. Import every model here so Alembic sees it."""

import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    Date,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.orm import Mapped, mapped_column

from app.db.base import SCHEMA, Base, CreatedAtMixin, IdMixin, TenantMixin


class AppUser(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """Standalone sign-in users (email + password). In AI Studio the studio's sign-in replaces this.

    ``email`` is stored lower-case and is unique across tenants (login does not ask for a tenant).
    """

    __tablename__ = "app_user"

    email: Mapped[str] = mapped_column(String(254), nullable=False, unique=True)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    password_hash: Mapped[str] = mapped_column(String(255), nullable=False)
    roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    failed_attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    locked_until: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    last_login_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class AuditEvent(TenantMixin, Base):
    """Append-only audit log with a per-tenant SHA-256 hash chain.

    UPDATE/DELETE are blocked by a database trigger (see migration 0001). Retain 7 years.
    """

    __tablename__ = "audit_event"
    __table_args__ = (
        UniqueConstraint("tenant_id", "seq", name="uq_audit_event_tenant_seq"),
        Index("ix_audit_event_tenant_entity", "tenant_id", "entity_type", "entity_id"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    seq: Mapped[int] = mapped_column(BigInteger, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_roles: Mapped[list[str]] = mapped_column(JSONB, nullable=False, default=list)
    auth_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    action: Mapped[str] = mapped_column(String(128), nullable=False)
    entity_type: Mapped[str] = mapped_column(String(64), nullable=False)
    entity_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    prev_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    hash: Mapped[str] = mapped_column(String(64), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Reference data (tenant-scoped). Seeded for now; later fed from the CRM catalog.
# ---------------------------------------------------------------------------

class Portfolio(IdMixin, TenantMixin, CreatedAtMixin, Base):
    __tablename__ = "portfolio"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_portfolio_tenant_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class Market(IdMixin, TenantMixin, CreatedAtMixin, Base):
    __tablename__ = "market"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_market_tenant_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    timezone: Mapped[str] = mapped_column(String(64), nullable=False)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class ProductFamily(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """Catalog hierarchy node. A family belongs to one portfolio; children share it."""

    __tablename__ = "product_family"
    __table_args__ = (UniqueConstraint("tenant_id", "code", name="uq_product_family_tenant_code"),)

    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.portfolio.id"), nullable=False, index=True)
    parent_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.product_family.id"), nullable=True)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


# ---------------------------------------------------------------------------
# Runs (assessments)
# ---------------------------------------------------------------------------

class Run(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """A governed rationalisation run and its scope (Define Scope, screen 02).

    ``owner_id`` is the identity-provider user id (string), so it works in studio mode too;
    ``owner_name`` is a display snapshot refreshed whenever the owner is set.
    """

    __tablename__ = "run"
    __table_args__ = (
        UniqueConstraint("tenant_id", "run_code", name="uq_run_tenant_code"),
        # Name must be unique among active (not CLOSED) runs of a tenant, case-insensitive.
        Index("uq_run_tenant_active_name", "tenant_id", text("lower(name)"), unique=True,
              postgresql_where=text("state <> 'CLOSED'")),
    )

    run_code: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    state: Mapped[str] = mapped_column(String(32), nullable=False)
    portfolio_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.portfolio.id"), nullable=False)
    market_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), nullable=False)
    product_family_ids: Mapped[list[uuid.UUID]] = mapped_column(ARRAY(Uuid), nullable=False)
    period_start: Mapped[date] = mapped_column(Date, nullable=False)
    period_end: Mapped[date] = mapped_column(Date, nullable=False)
    objectives: Mapped[list[str]] = mapped_column(ARRAY(String(40)), nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    owner_name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    # Evidence domains confirmed for this run (screen 03). NULL = not confirmed yet: the proposal from
    # the objectives applies (app.domain.evidence.propose_domains).
    evidence_domains: Mapped[list[str] | None] = mapped_column(ARRAY(String(40)), nullable=True)


# ---------------------------------------------------------------------------
# Versioned artifact registry (canonical schema, lookup, transform, crosswalk; later mappings,
# scoring, policy, prompts, models). Immutability is enforced by triggers (migration 0004).
# ---------------------------------------------------------------------------

class Artifact(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """A governed configuration item. ``key`` is unique per tenant across kinds (``KEY@1.0`` refs)."""

    __tablename__ = "artifact"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_artifact_tenant_key"),)

    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    key: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)


class ArtifactVersion(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """One version of an artifact. Only a DRAFT may change; see ``app.domain.artifacts``."""

    __tablename__ = "artifact_version"
    __table_args__ = (
        UniqueConstraint("artifact_id", "version_major", "version_minor", name="uq_artifact_version_number"),
        # At most one PUBLISHED and one DRAFT version per artifact.
        Index("uq_artifact_version_published", "artifact_id", unique=True,
              postgresql_where=text("status = 'PUBLISHED'")),
        Index("uq_artifact_version_draft", "artifact_id", unique=True,
              postgresql_where=text("status = 'DRAFT'")),
    )

    artifact_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.artifact.id"), nullable=False,
                                                   index=True)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    version_major: Mapped[int] = mapped_column(Integer, nullable=False)
    version_minor: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.artifact_version.id"), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    content: Mapped[dict] = mapped_column(JSONB, nullable=False)
    digest: Mapped[str] = mapped_column(String(71), nullable=False)
    valid_from: Mapped[date | None] = mapped_column(Date, nullable=True)
    valid_to: Mapped[date | None] = mapped_column(Date, nullable=True)
    change_note: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                 server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status_changed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_changed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Optimistic locking for drafts and status changes (not the artifact's semantic version).
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)


# ---------------------------------------------------------------------------
# Transactional outbox for domain events (written in the same transaction as the change;
# relayed by a polling loop in the worker).
# ---------------------------------------------------------------------------

class OutboxEvent(TenantMixin, Base):
    __tablename__ = "outbox_event"
    __table_args__ = (
        Index("ix_outbox_event_pending", "id", postgresql_where=text("published_at IS NULL")),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    event_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, unique=True, default=uuid.uuid4)
    event_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_type: Mapped[str] = mapped_column(String(64), nullable=False)
    aggregate_id: Mapped[str] = mapped_column(String(128), nullable=False)
    payload: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False,
                                                  server_default=func.now())
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_error: Mapped[str | None] = mapped_column(Text, nullable=True)


# ---------------------------------------------------------------------------
# Evidence Hub (screen 03): sources, uploads, discovered source schemas
# ---------------------------------------------------------------------------

class EvidenceSource(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """Where a run gets the evidence of one domain from. Excel uploads use one source per run + domain."""

    __tablename__ = "evidence_source"
    __table_args__ = (UniqueConstraint("run_id", "domain", "source_type", name="uq_evidence_source_run_domain_type"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)


class SourceSchemaVersion(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """Immutable column structure of a source (sheet + header row + columns). Same headers -> same version.

    UPDATE/DELETE are blocked by a trigger (migration 0005).
    """

    __tablename__ = "source_schema_version"
    __table_args__ = (
        UniqueConstraint("source_id", "version", name="uq_source_schema_version_number"),
        UniqueConstraint("source_id", "fingerprint", name="uq_source_schema_version_fingerprint"),
    )

    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_source.id"), nullable=False,
                                                 index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(71), nullable=False)
    sheet_name: Mapped[str] = mapped_column(String(64), nullable=False)
    header_row: Mapped[int] = mapped_column(Integer, nullable=False)
    columns: Mapped[list] = mapped_column(JSONB, nullable=False)
    template_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    template_match: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    first_upload_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)


class EvidenceUpload(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """One uploaded workbook. The file stays in quarantine until scanned and read; rejected files are
    deleted (only this record and the reasons remain)."""

    __tablename__ = "evidence_upload"
    __table_args__ = (
        # The same file twice in one source is a duplicate (unless the first one was rejected).
        Index("uq_evidence_upload_source_sha", "source_id", "sha256", unique=True,
              postgresql_where=text("status <> 'REJECTED'")),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_source.id"), nullable=False,
                                                 index=True)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    filename: Mapped[str] = mapped_column(String(255), nullable=False)
    size_bytes: Mapped[int] = mapped_column(BigInteger, nullable=False)
    sha256: Mapped[str] = mapped_column(String(64), nullable=False)
    storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reasons: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)  # [{code, message}]
    scan: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    discovery: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    uploaded_by: Mapped[str] = mapped_column(String(128), nullable=False)
    uploaded_by_name: Mapped[str] = mapped_column(String(120), nullable=False)
    processed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    schema_version_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey(f"{SCHEMA}.source_schema_version.id"), nullable=True)
    confirmed_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    confirmed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
