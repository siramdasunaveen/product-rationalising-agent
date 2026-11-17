import fakeredis
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from app.adapters.registry import build_platform
from app.db.models import AuditEvent
from app.main import create_app
from tests.conftest import PASSWORD

API = "/api/rationalisation"


def test_healthz(client):
    assert client.get("/healthz").json() == {"status": "ok"}


def test_readyz_ok(client):
    r = client.get("/readyz")
    assert r.status_code == 200
    assert r.json()["checks"] == {"database": "ok", "redis": "ok"}


def test_readyz_reports_failure(client):
    def broken():
        raise ConnectionError("redis down")

    client.app.state.readiness_checks["redis"] = broken
    r = client.get("/readyz")
    assert r.status_code == 503
    assert r.json()["checks"]["redis"].startswith("error")


def test_me_requires_token(client):
    assert client.get(f"{API}/me").status_code == 401
    assert client.get(f"{API}/me", headers={"Authorization": "Bearer junk"}).status_code == 401


def test_me_returns_permissions_for_persona(client, login, tenant):
    r = client.get(f"{API}/me", headers=login("portfolio_manager", tenant))
    assert r.status_code == 200
    body = r.json()
    assert body["tenant_id"] == tenant
    assert body["roles"] == ["portfolio_manager"]
    assert body["permissions"] == sorted(
        ["read", "run.create", "run.start", "candidate.view", "decision.submit", "registry.read", "evidence.upload"])
    assert body["platform_mode"] == "standalone"
    assert body["agent"]["name"] == "Product Rationalisation Agent"


def test_me_for_approver_has_scope(client, login):
    body = client.get(f"{API}/me", headers=login("architecture_approver")).json()
    assert "approval.act" in body["permissions"]
    assert "run.create" not in body["permissions"]
    assert body["approval_scopes"] == ["architecture"]


def test_manifest_nav(client, login):
    nav = client.get(f"{API}/manifest", headers=login("viewer")).json()["nav"]
    assert [n["label"] for n in nav] == [
        "Assessments", "Evidence Readiness", "Evidence Needed", "Rationalisation Opportunities",
        "Decision Approvals", "Transition Plans", "Outcomes & Benefits", "AI Runtime",
    ]
    by_key = {n["key"]: n for n in nav}
    assert by_key["transition-plans"]["status"] == "not_specified"
    assert by_key["assessments"]["sidebarLabel"] == "Assessment Overview"
    assert by_key["evidence-needed"]["sidebarLabel"] is None


def test_login_and_me_use_the_users_tenant_and_name(client, make_user, tenant):
    email = make_user("data_steward", tenant, name="Dana Kim")
    r = client.post(f"{API}/auth/login", json={"email": email.upper(), "password": PASSWORD})
    assert r.status_code == 200
    me = client.get(f"{API}/me", headers={"Authorization": f"Bearer {r.json()['access_token']}"}).json()
    assert (me["name"], me["tenant_id"], me["roles"]) == ("Dana Kim", tenant, ["data_steward"])


@pytest.mark.parametrize("email,password", [
    ("nobody@test.local", PASSWORD),  # unknown email
    (None, "wrong-password-123"),  # wrong password
])
def test_login_failures_look_the_same(client, make_user, email, password):
    email = email or make_user("viewer")
    r = client.post(f"{API}/auth/login", json={"email": email, "password": password})
    assert r.status_code == 401
    assert r.json()["detail"] == "Invalid email or password"


def test_login_is_audited(client, login, tenant, db):
    login("data_steward", tenant)
    events = db.execute(select(AuditEvent).where(AuditEvent.tenant_id == tenant)).scalars().all()
    assert [e.action for e in events] == ["auth.login_succeeded"]
    assert events[0].auth_context == {"method": "password"}


def test_login_not_mounted_in_studio_mode(settings, session_factory):
    studio = settings.model_copy(update={"platform_mode": "studio"})
    app = create_app(studio, platform=build_platform(studio, redis=fakeredis.FakeRedis()),
                     session_factory=session_factory)
    with TestClient(app) as c:
        assert c.post(f"{API}/auth/login", json={"email": "a@b.c", "password": "x"}).status_code == 404
        assert c.get("/healthz").status_code == 200
        assert "/api/rationalisation/auth/login" not in c.get("/openapi.json").json()["paths"]
