"""SQLAlchemy models. Import every model here so Alembic sees it."""

import uuid
from datetime import date, datetime
from typing import Any

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
    # Scoring & AI Route draft (screen 04): selected pack refs, run overrides, acknowledged warnings.
    configuration: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    started_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    # Incremented whenever the run's read models change (analysis completed, blocker state changed).
    projection_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


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
    # Several sources per domain are allowed (e.g. two CRM exports); names are unique within a run's domain.
    __table_args__ = (UniqueConstraint("run_id", "domain", "name", name="uq_evidence_source_run_domain_name"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False, index=True)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    # Which system the data comes from (e.g. SALESFORCE, DYNAMICS); precedence may name it as DOMAIN:SYSTEM.
    system: Mapped[str] = mapped_column(String(40), nullable=False, default="EXCEL")
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
    # The date the data describes ("data as of"), entered when confirming; freshness is measured from it.
    as_of_date: Mapped[date | None] = mapped_column(Date, nullable=True)


# ---------------------------------------------------------------------------
# Mapping Rules (step 5). Rules of a submitted version and all staged output are immutable (triggers in
# migration 0006).
# ---------------------------------------------------------------------------

class MappingDefinition(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """The mapping of one evidence source (run + domain) to the canonical schema; it has versions."""

    __tablename__ = "mapping_definition"
    __table_args__ = (UniqueConstraint("source_id", name="uq_mapping_definition_source"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False, index=True)
    source_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_source.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)


OPEN_MAPPING_STATUSES = ("DRAFT", "VALIDATING", "VALIDATION_FAILED", "READY_FOR_REVIEW")


class MappingVersion(IdMixin, TenantMixin, CreatedAtMixin, Base):
    __tablename__ = "mapping_version"
    __table_args__ = (
        UniqueConstraint("definition_id", "version", name="uq_mapping_version_number"),
        Index("uq_mapping_version_published", "definition_id", unique=True,
              postgresql_where=text("status = 'PUBLISHED'")),
        Index("uq_mapping_version_open", "definition_id", unique=True,
              postgresql_where=text("status IN ('DRAFT', 'VALIDATING', 'VALIDATION_FAILED', 'READY_FOR_REVIEW')")),
    )

    definition_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.mapping_definition.id"), nullable=False,
                                                     index=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    parent_version_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.mapping_version.id"),
                                                                nullable=True)
    source_schema_version_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey(f"{SCHEMA}.source_schema_version.id"), nullable=False)
    canonical_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    canonical_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    status: Mapped[str] = mapped_column(String(24), nullable=False)
    content_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    warnings_acknowledged: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    lock_version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    submitted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    approved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    approved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    published_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)


class MappingRuleRow(IdMixin, TenantMixin, Base):
    __tablename__ = "mapping_rule"
    __table_args__ = (UniqueConstraint("version_id", "rule_key", name="uq_mapping_rule_key"),)

    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.mapping_version.id"), nullable=False,
                                                  index=True)
    rule_key: Mapped[str] = mapped_column(String(16), nullable=False)
    position: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_type: Mapped[str] = mapped_column(String(16), nullable=False)
    source_column: Mapped[str | None] = mapped_column(String(255), nullable=True)
    target_entity: Mapped[str | None] = mapped_column(String(64), nullable=True)
    target_field: Mapped[str | None] = mapped_column(String(64), nullable=True)
    params: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)  # refs, derived, relationship
    required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    null_handling: Mapped[str] = mapped_column(String(16), nullable=False)
    default_value: Mapped[str | None] = mapped_column(String(255), nullable=True)
    suggestion_confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)
    proposed_by: Mapped[str] = mapped_column(String(16), nullable=False)


class MappingValidationRun(IdMixin, TenantMixin, CreatedAtMixin, Base):
    __tablename__ = "mapping_validation_run"

    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.mapping_version.id"), nullable=False,
                                                  index=True)
    upload_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_upload.id"), nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)  # the rules that were validated
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # RUNNING | PASS | WARNING | BLOCKED | FAILED
    result: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class MappingApproval(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """Review decision on an exact version and content hash. Append-only."""

    __tablename__ = "mapping_approval"

    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.mapping_version.id"), nullable=False,
                                                  index=True)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # APPROVE | RETURN | REJECT
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False)


class MappingExecution(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """One apply of a mapping version to one upload. Idempotent per (file hash, mapping version)."""

    __tablename__ = "mapping_execution"
    __table_args__ = (UniqueConstraint("tenant_id", "execution_key", name="uq_mapping_execution_key"),
                      UniqueConstraint("tenant_id", "idempotency_key", name="uq_mapping_execution_idem"))

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False, index=True)
    upload_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_upload.id"), nullable=False)
    mapping_version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.mapping_version.id"),
                                                          nullable=False)
    execution_key: Mapped[str] = mapped_column(String(71), nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(String(128), nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # QUEUED | RUNNING | SUCCEEDED | FAILED
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    counts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    rejected_rows: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    conflicts: Mapped[list | None] = mapped_column(JSONB, nullable=True)
    output_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class StagedRecord(IdMixin, TenantMixin, Base):
    """A canonical record produced by one mapping execution (not yet reconciled). Append-only."""

    __tablename__ = "staged_record"
    __table_args__ = (UniqueConstraint("execution_id", "entity", "record_key", name="uq_staged_record_key"),)

    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.mapping_execution.id"), nullable=False,
                                                    index=True)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    source_rows: Mapped[list[int]] = mapped_column(ARRAY(Integer), nullable=False)


class StagedRelationship(TenantMixin, Base):
    __tablename__ = "staged_relationship"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.mapping_execution.id"), nullable=False,
                                                    index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    from_entity: Mapped[str] = mapped_column(String(64), nullable=False)
    from_key: Mapped[str] = mapped_column(String(512), nullable=False)
    to_entity: Mapped[str] = mapped_column(String(64), nullable=False)
    to_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    rule_key: Mapped[str] = mapped_column(String(16), nullable=False)


class FieldLineage(TenantMixin, Base):
    """Where one staged field value came from (spec: lineage record)."""

    __tablename__ = "field_lineage"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    execution_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.mapping_execution.id"), nullable=False,
                                                    index=True)
    staged_record_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.staged_record.id"), nullable=False,
                                                        index=True)
    canonical_entity: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_field: Mapped[str] = mapped_column(String(64), nullable=False)
    canonical_value: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON text
    source_system: Mapped[str] = mapped_column(String(64), nullable=False)
    source_key: Mapped[str] = mapped_column(String(512), nullable=False)
    source_field: Mapped[str | None] = mapped_column(String(255), nullable=True)
    source_value: Mapped[str | None] = mapped_column(Text, nullable=True)
    upload_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    sheet: Mapped[str] = mapped_column(String(64), nullable=False)
    source_row: Mapped[int] = mapped_column(Integer, nullable=False)
    mapping_rule_key: Mapped[str] = mapped_column(String(16), nullable=False)
    mapping_type: Mapped[str] = mapped_column(String(16), nullable=False)
    lookup_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    transform_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    crosswalk_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    mapping_version_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)  # set by step 6 snapshots


# ---------------------------------------------------------------------------
# Reconciliation and evidence snapshots (step 6). The reconciliation tables are the working set (re-built by each
# reconciliation, updated by Data Steward decisions); decisions and snapshots are immutable (migration 0007).
# ---------------------------------------------------------------------------

class ReconciliationRun(IdMixin, TenantMixin, CreatedAtMixin, Base):
    __tablename__ = "reconciliation_run"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False, index=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # QUEUED | RUNNING | SUCCEEDED | FAILED
    policy_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    canonical_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    reconciled_on: Mapped[date] = mapped_column(Date, nullable=False)  # "today" for freshness
    inputs: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    counts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)  # stale, dangling, possible duplicates
    result_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ReconciledRecord(IdMixin, TenantMixin, Base):
    __tablename__ = "reconciled_record"
    __table_args__ = (UniqueConstraint("reconciliation_id", "entity", "record_key", name="uq_reconciled_record_key"),)

    reconciliation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.reconciliation_run.id"),
                                                         nullable=False, index=True)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    values: Mapped[dict] = mapped_column(JSONB, nullable=False)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False)


class ReconciledRelationship(TenantMixin, Base):
    __tablename__ = "reconciled_relationship"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    reconciliation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.reconciliation_run.id"),
                                                         nullable=False, index=True)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    from_entity: Mapped[str] = mapped_column(String(64), nullable=False)
    from_key: Mapped[str] = mapped_column(String(512), nullable=False)
    to_entity: Mapped[str] = mapped_column(String(64), nullable=False)
    to_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False)


class ReconciliationConflict(IdMixin, TenantMixin, Base):
    __tablename__ = "reconciliation_conflict"
    __table_args__ = (UniqueConstraint("reconciliation_id", "conflict_key", name="uq_reconciliation_conflict_key"),)

    reconciliation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.reconciliation_run.id"),
                                                         nullable=False, index=True)
    conflict_key: Mapped[str] = mapped_column(String(32), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    material: Mapped[bool] = mapped_column(Boolean, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    candidates: Mapped[list] = mapped_column(JSONB, nullable=False)
    recommended_source_id: Mapped[str] = mapped_column(String(64), nullable=False)
    reason: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # OPEN | AUTO_RESOLVED | RESOLVED | DEFERRED
    decision_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class StewardDecisionRow(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """A Data Steward's decision on a conflict. Append-only; applies while the conflicting values are unchanged."""

    __tablename__ = "steward_decision"
    __table_args__ = (Index("ix_steward_decision_conflict", "run_id", "conflict_key", "fingerprint"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    conflict_key: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(32), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)  # SELECT_SOURCE | MANUAL | DEFER
    source_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    rationale: Mapped[str | None] = mapped_column(Text, nullable=True)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_name: Mapped[str] = mapped_column(String(120), nullable=False)


class EvidenceSnapshot(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """An approved, immutable evidence snapshot (SNAP-001 ...). Only its status may change (APPROVED -> SUPERSEDED)."""

    __tablename__ = "evidence_snapshot"
    __table_args__ = (
        UniqueConstraint("run_id", "snapshot_no", name="uq_evidence_snapshot_number"),
        Index("uq_evidence_snapshot_current", "run_id", unique=True, postgresql_where=text("status = 'APPROVED'")),
    )

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False, index=True)
    snapshot_no: Mapped[int] = mapped_column(Integer, nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    parent_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_snapshot.id"),
                                                                 nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # APPROVED | SUPERSEDED
    reconciliation_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.reconciliation_run.id"),
                                                         nullable=False)
    policy_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    policy_digest: Mapped[str] = mapped_column(String(71), nullable=False)
    canonical_ref: Mapped[str] = mapped_column(String(80), nullable=False)
    inputs: Mapped[list] = mapped_column(JSONB, nullable=False)
    counts: Mapped[dict] = mapped_column(JSONB, nullable=False)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    approved_by: Mapped[str] = mapped_column(String(128), nullable=False)
    approved_by_name: Mapped[str] = mapped_column(String(120), nullable=False)
    approved_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SnapshotFact(TenantMixin, Base):
    """One canonical field value in a snapshot, with a stable evidence id (EV-...). Append-only."""

    __tablename__ = "snapshot_fact"
    __table_args__ = (UniqueConstraint("snapshot_id", "evidence_id", name="uq_snapshot_fact_evidence_id"),
                      Index("ix_snapshot_fact_record", "snapshot_id", "entity", "record_key"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_snapshot.id"), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(20), nullable=False)
    entity: Mapped[str] = mapped_column(String(64), nullable=False)
    record_key: Mapped[str] = mapped_column(String(512), nullable=False)
    field: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[Any] = mapped_column(JSONB, nullable=True)
    provenance: Mapped[dict] = mapped_column(JSONB, nullable=False)
    stale: Mapped[bool] = mapped_column(Boolean, nullable=False)
    conflicted: Mapped[bool] = mapped_column(Boolean, nullable=False)


class SnapshotRelationship(TenantMixin, Base):
    __tablename__ = "snapshot_relationship"
    __table_args__ = (Index("ix_snapshot_relationship_from", "snapshot_id", "from_entity", "from_key"),
                      Index("ix_snapshot_relationship_to", "snapshot_id", "to_entity", "to_key"))

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_snapshot.id"), nullable=False)
    evidence_id: Mapped[str] = mapped_column(String(20), nullable=False)
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    from_entity: Mapped[str] = mapped_column(String(64), nullable=False)
    from_key: Mapped[str] = mapped_column(String(512), nullable=False)
    to_entity: Mapped[str] = mapped_column(String(64), nullable=False)
    to_key: Mapped[str] = mapped_column(String(512), nullable=False)
    sources: Mapped[list] = mapped_column(JSONB, nullable=False)


# ---------------------------------------------------------------------------
# AI runtime (step 8)
# ---------------------------------------------------------------------------

class AISecret(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """Credential of an AI deployment (e.g. an API key), Fernet-encrypted. Never returned by the API."""

    __tablename__ = "ai_secret"
    __table_args__ = (UniqueConstraint("tenant_id", "name", name="uq_ai_secret_tenant_name"),)

    name: Mapped[str] = mapped_column(String(64), nullable=False)
    ciphertext: Mapped[str] = mapped_column(Text, nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AIInvocation(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """One governed AI task execution (append-only): which profile/deployment/model answered, or the fallback."""

    __tablename__ = "ai_invocation"

    run_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    task: Mapped[str] = mapped_column(String(40), nullable=False)
    profile_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    deployment_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # llm | fallback
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    chain: Mapped[list] = mapped_column(JSONB, nullable=False)  # deployment refs tried, in order
    errors: Mapped[list] = mapped_column(JSONB, nullable=False)
    needs_human_review: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    request_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    output_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)


class RunManifest(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """What a run is pinned to when it starts (screen 05): exact artifact refs + digests, the approved snapshot,
    resolved thresholds and treatments with their source, AI routes, acknowledged limitations. Immutable."""

    __tablename__ = "run_manifest"
    __table_args__ = (UniqueConstraint("run_id", name="uq_run_manifest_run"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    manifest: Mapped[dict] = mapped_column(JSONB, nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)


# ---------------------------------------------------------------------------
# Analysis (step 10): runs of the workflow, detector findings, candidates
# ---------------------------------------------------------------------------

class AnalysisRun(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """One execution of the analysis workflow for a run (the first after Start; later ones after accepted
    evidence). Stages: Discover, Normalize, Analyze, Assess, Recommend (system) then Review, Approve (people)."""

    __tablename__ = "analysis_run"
    __table_args__ = (UniqueConstraint("run_id", "seq", name="uq_analysis_run_seq"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False, index=True)
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    reason: Mapped[str] = mapped_column(String(40), nullable=False)  # START | REASSESSMENT
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # QUEUED | RUNNING | SUCCEEDED | FAILED
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_snapshot.id"), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    stages: Mapped[list] = mapped_column(JSONB, nullable=False)
    source_products: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    counts: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    detector_summary: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    result_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    job_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class DetectorFinding(IdMixin, TenantMixin, CreatedAtMixin, Base):
    __tablename__ = "detector_finding"

    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.analysis_run.id"), nullable=False,
                                                   index=True)
    run_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False, index=True)
    finding_key: Mapped[str] = mapped_column(String(32), nullable=False)
    detector_type: Mapped[str] = mapped_column(String(32), nullable=False)
    detector_version: Mapped[str] = mapped_column(String(16), nullable=False)
    subjects: Mapped[list] = mapped_column(JSONB, nullable=False)
    signal: Mapped[int] = mapped_column(Integer, nullable=False)  # per mille
    threshold: Mapped[int] = mapped_column(Integer, nullable=False)
    band: Mapped[str] = mapped_column(String(16), nullable=False)
    factors: Mapped[dict] = mapped_column(JSONB, nullable=False)
    coverage: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    notes: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ATTACHED | MERGED | INVALID | OUT_OF_SCOPE
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class Candidate(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """A normalized rationalisation opportunity. Never deleted: INVALID / OUT_OF_SCOPE / SUPERSEDED keep lineage."""

    __tablename__ = "candidate"
    __table_args__ = (UniqueConstraint("analysis_id", "group_key", name="uq_candidate_analysis_group"),)

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False, index=True)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.analysis_run.id"), nullable=False,
                                                   index=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    group_key: Mapped[str] = mapped_column(String(71), nullable=False)
    subjects: Mapped[list] = mapped_column(JSONB, nullable=False)
    primary_type: Mapped[str] = mapped_column(String(32), nullable=False)
    supporting_types: Mapped[list] = mapped_column(JSONB, nullable=False)
    max_signal: Mapped[int] = mapped_column(Integer, nullable=False)
    finding_count: Mapped[int] = mapped_column(Integer, nullable=False)
    evidence_ids: Mapped[list] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ACTIVE | INVALID | OUT_OF_SCOPE | SUPERSEDED
    superseded_by: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    lineage: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    # Step 11 assessment (all deterministic; see app.domain.assessment)
    similarity: Mapped[int | None] = mapped_column(Integer, nullable=True)  # per mille, primary detector
    opportunity_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100
    score_inputs: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    score_coverage: Mapped[int | None] = mapped_column(Integer, nullable=True)
    dimensions: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    guardrail: Mapped[str | None] = mapped_column(String(20), nullable=True)
    recommendation: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    impact: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    priority_index: Mapped[int | None] = mapped_column(Integer, nullable=True)
    base_priority: Mapped[str | None] = mapped_column(String(10), nullable=True)
    effective_priority: Mapped[str | None] = mapped_column(String(10), nullable=True)
    priority_detail: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    candidate_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    hidden: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    assessment_hash: Mapped[str | None] = mapped_column(String(71), nullable=True)


class Blocker(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """A persisted reason the workflow cannot proceed (or a warning). Created and resolved by services as facts
    change; never derived on the fly. Counted on Run Overview when active, BLOCKED, applicable, not superseded."""

    __tablename__ = "blocker"
    __table_args__ = (UniqueConstraint("run_id", "code", name="uq_blocker_run_code"),)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    analysis_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.analysis_run.id"), nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True, index=True)
    candidate_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    code: Mapped[str] = mapped_column(String(16), nullable=False)  # BLK-0001 per run
    dedupe_key: Mapped[str] = mapped_column(String(300), nullable=False)
    rule: Mapped[str] = mapped_column(String(40), nullable=False)
    category: Mapped[str] = mapped_column(String(16), nullable=False)  # EVIDENCE|VALIDATION|POLICY|DEPENDENCY|WORKFLOW
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # OPEN | IN_PROGRESS | RESOLVED | WAIVED
    consequence: Mapped[str] = mapped_column(String(16), nullable=False)  # BLOCKED | WARNING_ONLY
    severity: Mapped[str] = mapped_column(String(10), nullable=False)
    domain: Mapped[str] = mapped_column(String(16), nullable=False)
    subject: Mapped[str] = mapped_column(String(255), nullable=False)
    message: Mapped[str] = mapped_column(Text, nullable=False)
    owner_role: Mapped[str | None] = mapped_column(String(40), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    applicable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    superseded: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    facts: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    resolved_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    resolved_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    resolution: Mapped[str | None] = mapped_column(Text, nullable=True)


class CandidateComment(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """Discussion on Candidate Review: human collaboration only, never evidence. Append-only; author by id."""

    __tablename__ = "candidate_comment"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    candidate_code: Mapped[str] = mapped_column(String(16), nullable=False)
    author_id: Mapped[str] = mapped_column(String(128), nullable=False)
    body: Mapped[str] = mapped_column(Text, nullable=False)


class CandidateExplanation(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """An accepted recommendation explanation (validated LLM output or the deterministic template). Append-only.
    AI confidence lives here only; it never feeds a score, priority, readiness or approval."""

    __tablename__ = "candidate_explanation"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    candidate_code: Mapped[str] = mapped_column(String(16), nullable=False)
    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    source: Mapped[str] = mapped_column(String(16), nullable=False)  # llm | fallback
    deployment_ref: Mapped[str | None] = mapped_column(String(80), nullable=True)
    model: Mapped[str | None] = mapped_column(String(120), nullable=True)
    sentences: Mapped[list] = mapped_column(JSONB, nullable=False)  # [{text, evidenceIds}]
    confidence: Mapped[int | None] = mapped_column(Integer, nullable=True)  # per mille, LLM only
    below_threshold: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)


class SavedView(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """A personal Candidate Explorer view: configuration only (filters, sort, columns), never candidate copies."""

    __tablename__ = "saved_view"
    __table_args__ = (UniqueConstraint("tenant_id", "owner_id", "name", name="uq_saved_view_owner_name"),)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(80), nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)  # PERSONAL (team views are built in)
    filters: Mapped[dict] = mapped_column(JSONB, nullable=False)
    sort: Mapped[list] = mapped_column(JSONB, nullable=False)
    visible_columns: Mapped[list] = mapped_column(JSONB, nullable=False)


class EvidenceRequest(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """A logical evidence contract for one gap of one candidate (ER-101 ...). The user chooses owner, due date
    and fulfilment channel; the system owns the contract and drives the status."""

    __tablename__ = "evidence_request"
    __table_args__ = (UniqueConstraint("run_id", "code", name="uq_evidence_request_run_code"),)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    candidate_code: Mapped[str] = mapped_column(String(16), nullable=False)
    candidate_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    blocker_code: Mapped[str | None] = mapped_column(String(16), nullable=True)
    blocker_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    action: Mapped[str | None] = mapped_column(String(16), nullable=True)  # emerging disposition
    trigger: Mapped[str] = mapped_column(String(200), nullable=False)
    evidence_needed: Mapped[str] = mapped_column(String(200), nullable=False)
    decision_impact: Mapped[str] = mapped_column(String(200), nullable=False)
    consequence: Mapped[str] = mapped_column(String(16), nullable=False)  # BLOCKED | WARNING_ONLY
    priority: Mapped[str] = mapped_column(String(10), nullable=False)
    contract: Mapped[dict] = mapped_column(JSONB, nullable=False)
    owner_role: Mapped[str] = mapped_column(String(40), nullable=False)
    owner_id: Mapped[str | None] = mapped_column(String(128), nullable=True)
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    fulfilment_method: Mapped[str | None] = mapped_column(String(24), nullable=True)
    fulfilment_job: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    status_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_snapshot.id"), nullable=False)
    baseline_executions: Mapped[list] = mapped_column(JSONB, nullable=False)
    received_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    accepted_snapshot_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reassessment_analysis_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    reassessment_status: Mapped[str] = mapped_column(String(16), nullable=False, default="NONE")
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class EvidenceRequestEvent(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """Append-only lifecycle history of an evidence request."""

    __tablename__ = "evidence_request_event"

    request_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.evidence_request.id"), nullable=False)
    from_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    to_status: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)


class Decision(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """The human-owned decision of one candidate (DEC-0001 per run). Content lives in immutable versions; this
    row tracks the current version and state. Approval never changes a product."""

    __tablename__ = "decision"
    __table_args__ = (UniqueConstraint("run_id", "code", name="uq_decision_run_code"),
                      UniqueConstraint("run_id", "candidate_code", name="uq_decision_run_candidate"))

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    code: Mapped[str] = mapped_column(String(16), nullable=False)
    candidate_code: Mapped[str] = mapped_column(String(16), nullable=False)
    current_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    current_version_no: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # DRAFT|SUBMITTED|ON_HOLD|RETURNED|APPROVED|REJECTED|SUPERSEDED
    approved_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)


class DecisionVersion(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """An immutable decision version (append-only): the content, what it was taken on, its hash and route."""

    __tablename__ = "decision_version"
    __table_args__ = (UniqueConstraint("decision_id", "version_no", name="uq_decision_version_no"),)

    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.decision.id"), nullable=False)
    version_no: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    analysis_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    snapshot_code: Mapped[str] = mapped_column(String(16), nullable=False)
    manifest_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    recommendation: Mapped[dict] = mapped_column(JSONB, nullable=False)
    disposition: Mapped[str] = mapped_column(String(16), nullable=False)
    target_product_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    effective_date: Mapped[date] = mapped_column(Date, nullable=False)
    owner_id: Mapped[str] = mapped_column(String(128), nullable=False)
    rationale: Mapped[str] = mapped_column(Text, nullable=False)
    commercial_treatment: Mapped[str | None] = mapped_column(String(200), nullable=True)
    conditions: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    readiness: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    route: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)  # why this version exists (edit, return, reassessment)


class ApprovalTask(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """One required approval scope of a submitted decision version."""

    __tablename__ = "approval_task"

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.decision.id"), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.decision_version.id"), nullable=False)
    scope: Mapped[str] = mapped_column(String(40), nullable=False)
    state: Mapped[str] = mapped_column(String(16), nullable=False)  # PENDING|APPROVED|REJECTED|RETURNED|ON_HOLD|CANCELLED
    due_date: Mapped[date | None] = mapped_column(Date, nullable=True)
    acted_by: Mapped[str | None] = mapped_column(String(128), nullable=True)
    acted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)


class ApprovalAction(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """Append-only approval evidence: actor, roles, auth context, version, hash, policy result, comment.
    Retained for the policy pack's auditRetentionYears (7)."""

    __tablename__ = "approval_action"

    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    decision_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.decision.id"), nullable=False)
    version_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.decision_version.id"), nullable=False)
    task_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    scope: Mapped[str | None] = mapped_column(String(40), nullable=True)
    action: Mapped[str] = mapped_column(String(16), nullable=False)  # SUBMIT|APPROVE|RETURN|HOLD|REJECT|SUPERSEDE
    actor_id: Mapped[str] = mapped_column(String(128), nullable=False)
    actor_roles: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    auth_context: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    content_hash: Mapped[str] = mapped_column(String(71), nullable=False)
    policy_result: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    comment: Mapped[str | None] = mapped_column(Text, nullable=True)
    resulting_state: Mapped[str] = mapped_column(String(16), nullable=False)


class Connector(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """An approved live source system (step 15). The OAuth client secret is Fernet-encrypted; never returned."""

    __tablename__ = "connector"
    __table_args__ = (UniqueConstraint("tenant_id", "key", name="uq_connector_tenant_key"),)

    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False, server_default=func.now())
    key: Mapped[str] = mapped_column(String(40), nullable=False)
    name: Mapped[str] = mapped_column(String(120), nullable=False)
    system: Mapped[str] = mapped_column(String(40), nullable=False)  # SALESFORCE
    config: Mapped[dict] = mapped_column(JSONB, nullable=False)  # instanceUrl, apiVersion, authType, queries
    client_id: Mapped[str | None] = mapped_column(String(255), nullable=True)
    secret_ciphertext: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="ACTIVE")  # ACTIVE | DISABLED
    last_check: Mapped[dict | None] = mapped_column(JSONB, nullable=True)
    created_by: Mapped[str] = mapped_column(String(128), nullable=False)
    updated_by: Mapped[str] = mapped_column(String(128), nullable=False)


class ConnectorPull(IdMixin, TenantMixin, CreatedAtMixin, Base):
    """One extraction from a connector into a run's domain (lineage from the live system to the upload)."""

    __tablename__ = "connector_pull"

    connector_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.connector.id"), nullable=False)
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey(f"{SCHEMA}.run.id"), nullable=False)
    domain: Mapped[str] = mapped_column(String(40), nullable=False)
    upload_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # SUCCEEDED | FAILED
    rows: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    soql: Mapped[str] = mapped_column(Text, nullable=False)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    requested_by: Mapped[str] = mapped_column(String(128), nullable=False)
