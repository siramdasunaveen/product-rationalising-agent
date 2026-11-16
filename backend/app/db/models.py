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
