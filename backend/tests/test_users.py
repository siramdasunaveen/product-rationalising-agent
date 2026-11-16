from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from app.adapters.local.audit import DbAuditLog
from app.adapters.local.passwords import hash_password, verify_password
from app.db.models import AppUser, AuditEvent
from app.services.users import (
    LOCKOUT,
    MAX_FAILED_ATTEMPTS,
    LoginFailed,
    UserError,
    authenticate,
    create_user,
    set_password,
)
from tests.conftest import PASSWORD

AUDIT = DbAuditLog()


def test_password_hash_round_trip():
    h = hash_password("s3cret-password")
    assert h.startswith("scrypt$") and "s3cret" not in h
    assert verify_password("s3cret-password", h)
    assert not verify_password("wrong", h)
    assert not verify_password("x", "garbage")
    assert hash_password("same") != hash_password("same")  # salted


@pytest.mark.parametrize("kwargs,message", [
    ({"email": "not-an-email"}, "valid email"),
    ({"password": "short"}, "at least"),
    ({"roles": ["root"]}, "Roles"),
    ({"roles": []}, "Roles"),
    ({"name": "  "}, "Name"),
    ({"tenant_id": "bad tenant!"}, "Tenant"),
])
def test_create_user_validation(db, kwargs, message):
    args = {"email": "x@test.local", "name": "X", "password": PASSWORD, "roles": ["viewer"]} | kwargs
    with pytest.raises(UserError, match=message):
        create_user(db, **args)
    db.rollback()


def test_email_is_unique_case_insensitive(db, make_user):
    email = make_user("viewer")
    with pytest.raises(UserError, match="already exists"):
        create_user(db, email=email.upper(), name="Dup", password=PASSWORD, roles=["viewer"])
    db.rollback()


def test_success_resets_counters_and_records_login(db, make_user):
    email = make_user("viewer")
    with pytest.raises(LoginFailed):
        authenticate(db, AUDIT, email=email, password="wrong-password-1")
    user = authenticate(db, AUDIT, email=f"  {email.upper()} ", password=PASSWORD)
    assert user.failed_attempts == 0 and user.last_login_at is not None


def test_lockout_after_repeated_failures(db, make_user):
    email = make_user("viewer")
    now = datetime.now(UTC)
    for _ in range(MAX_FAILED_ATTEMPTS):
        with pytest.raises(LoginFailed):
            authenticate(db, AUDIT, email=email, password="wrong-password-1", now=now)

    # Locked: even the right password fails.
    with pytest.raises(LoginFailed):
        authenticate(db, AUDIT, email=email, password=PASSWORD, now=now + timedelta(minutes=1))
    # After the lockout period it works again.
    user = authenticate(db, AUDIT, email=email, password=PASSWORD, now=now + LOCKOUT + timedelta(seconds=1))
    assert user.locked_until is None


def test_inactive_user_cannot_sign_in(db, make_user):
    email = make_user("viewer", active=False)
    with pytest.raises(LoginFailed):
        authenticate(db, AUDIT, email=email, password=PASSWORD)


def test_failed_logins_are_audited_with_reason(db, make_user, tenant):
    email = make_user("viewer", tenant)
    with pytest.raises(LoginFailed):
        authenticate(db, AUDIT, email=email, password="wrong-password-1")
    user = db.scalar(select(AppUser).where(AppUser.email == email))
    events = db.scalars(select(AuditEvent).where(AuditEvent.tenant_id == tenant)).all()
    assert [(e.action, e.payload) for e in events] == [("auth.login_failed", {"reason": "wrong_password"})]
    assert events[0].actor_id == str(user.id)
    assert AUDIT.verify_chain(tenant, session=db).ok


def test_set_password_replaces_password_and_clears_lockout(db, make_user):
    email = make_user("viewer")
    user = db.scalar(select(AppUser).where(AppUser.email == email))
    user.failed_attempts, user.locked_until = 3, datetime.now(UTC) + LOCKOUT
    set_password(user, "Demo@123")
    db.commit()
    assert authenticate(db, AUDIT, email=email, password="Demo@123").id == user.id
    with pytest.raises(LoginFailed):
        authenticate(db, AUDIT, email=email, password=PASSWORD)
    with pytest.raises(UserError):
        set_password(user, "short")
