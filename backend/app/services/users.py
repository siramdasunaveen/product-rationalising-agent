"""Standalone users: create, and sign in with email + password (lockout + audit)."""

import logging
import re
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.local.passwords import (
    DUMMY_HASH,
    MIN_PASSWORD_LENGTH,
    hash_password,
    verify_password,
)
from app.db.models import AppUser
from app.domain.permissions import PERSONAS
from app.ports.audit import AuditLog, AuditRecord

log = logging.getLogger("pra.auth")

MAX_FAILED_ATTEMPTS = 5
LOCKOUT = timedelta(minutes=15)
_EMAIL = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
_TENANT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")


class UserError(ValueError):
    """Invalid input when creating a user."""


class LoginFailed(Exception):
    """Generic failure; the message shown to the user never says why."""


@dataclass(frozen=True)
class DemoUser:
    email: str
    name: str
    role: str


DEMO_USERS = [
    DemoUser("pm@demo.local", "Anika Patel", "portfolio_manager"),
    DemoUser("po@demo.local", "Marco Rossi", "product_owner"),
    DemoUser("steward@demo.local", "Dana Kim", "data_steward"),
    DemoUser("evidence@demo.local", "Leo Novak", "evidence_owner"),
    DemoUser("commercial@demo.local", "Sara Ahmed", "commercial_approver"),
    DemoUser("product@demo.local", "Tom Becker", "product_approver"),
    DemoUser("architecture@demo.local", "Mei Chen", "architecture_approver"),
    DemoUser("ea@demo.local", "Ravi Iyer", "enterprise_architect"),
    DemoUser("admin@demo.local", "Agent Admin", "agent_admin"),
    DemoUser("viewer@demo.local", "Victor Viewer", "viewer"),
]


def normalise_email(email: str) -> str:
    return email.strip().lower()


def create_user(session: Session, *, email: str, name: str, password: str, roles: list[str],
                tenant_id: str = "demo") -> AppUser:
    email = normalise_email(email)
    if not _EMAIL.match(email):
        raise UserError(f"Not a valid email: {email}")
    if not name.strip():
        raise UserError("Name is required")
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    if not roles or any(r not in PERSONAS for r in roles):
        raise UserError(f"Roles must be from: {', '.join(PERSONAS)}")
    if not _TENANT.match(tenant_id):
        raise UserError("Tenant id may contain letters, digits, '_' and '-' only")
    if session.scalar(select(AppUser.id).where(AppUser.email == email)):
        raise UserError(f"A user with email {email} already exists")

    user = AppUser(tenant_id=tenant_id, email=email, name=name.strip(),
                   password_hash=hash_password(password), roles=list(roles), is_active=True,
                   failed_attempts=0)
    session.add(user)
    session.flush()
    return user


def set_password(user: AppUser, password: str) -> None:
    """Replace the password and clear any lockout."""
    if len(password) < MIN_PASSWORD_LENGTH:
        raise UserError(f"Password must be at least {MIN_PASSWORD_LENGTH} characters")
    user.password_hash = hash_password(password)
    user.failed_attempts = 0
    user.locked_until = None


def _audit(audit: AuditLog, session: Session, user: AppUser, action: str, reason: str | None = None) -> None:
    audit.record(
        AuditRecord(
            tenant_id=user.tenant_id,
            actor_id=str(user.id),
            actor_roles=tuple(user.roles),
            auth_context={"method": "password"},
            action=action,
            entity_type="user",
            entity_id=str(user.id),
            payload={"reason": reason} if reason else {},
        ),
        session=session,
    )


def authenticate(session: Session, audit: AuditLog, *, email: str, password: str,
                 now: datetime | None = None) -> AppUser:
    """Return the user if the password is right. Commits the attempt (counters + audit)."""
    now = now or datetime.now(UTC)
    user = session.scalar(select(AppUser).where(AppUser.email == normalise_email(email)).with_for_update())

    if user is None:
        verify_password(password, DUMMY_HASH)  # same timing as a real check
        log.info("login failed: unknown email")
        raise LoginFailed()

    reason = None
    if not user.is_active:
        reason = "inactive"
    elif user.locked_until and user.locked_until > now:
        reason = "locked"
    elif not verify_password(password, user.password_hash):
        reason = "wrong_password"
        user.failed_attempts += 1
        if user.failed_attempts >= MAX_FAILED_ATTEMPTS:
            user.locked_until = now + LOCKOUT
            user.failed_attempts = 0
            reason = "wrong_password_locked"

    if reason:
        _audit(audit, session, user, "auth.login_failed", reason)
        session.commit()
        raise LoginFailed()

    user.failed_attempts = 0
    user.locked_until = None
    user.last_login_at = now
    _audit(audit, session, user, "auth.login_succeeded")
    session.commit()
    return user
