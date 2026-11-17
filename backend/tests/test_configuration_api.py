"""Step 9 end to end: Scoring & AI Route, Review & Start, Run Manifest pinned against newer artifacts."""

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import AuditEvent, OutboxEvent
from app.domain.artifacts import ArtifactKind, Bump
from app.services import artifacts as registry
from app.services.users import create_user
from tests.conftest import PASSWORD
from tests.test_reconciliation_api import (  # noqa: F401 - env is a fixture
    CRM,
    R,
    apply,
    default_source,
    env,
    load,
    publish_mapping,
    reconcile,
)


def cfg(env, who="pm"):
    r = env["client"].get(f"{R(env)}/configuration", headers=env[who])
    assert r.status_code == 200, r.text
    return r.json()


def by_role(body, role):
    return next(s for s in body["selections"] if s["role"] == role)


def setting(body, name):
    return next(s for s in body["settings"] if s["name"] == name)


def approved_snapshot(env):
    uid = load(env, "CRM_CATALOG", CRM, [["PRD-1", "Mobile Data 15GB", "Postpaid", "Active", None]])
    apply(env, uid, publish_mapping(env, "CRM_CATALOG", default_source(env, "CRM_CATALOG")))
    rec = reconcile(env)["latest_succeeded"]
    r = env["client"].post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]})
    assert r.status_code == 201, r.text


def two_role_user(env, roles):
    email = f"cfg-{uuid.uuid4().hex[:8]}@test.local"
    with env["sf"]() as s:
        create_user(s, email=email, name="Configurator", password=PASSWORD, roles=roles, tenant_id=env["tenant"])
        s.commit()
    t = env["client"].post("/api/rationalisation/auth/login", json={"email": email, "password": PASSWORD}).json()
    return {"Authorization": f"Bearer {t['access_token']}"}


def test_recommendations_settings_and_blockers(env):
    body = cfg(env)
    assert body["state"] == "CONFIGURING" and body["started"] is False
    assert by_role(body, "detectorPack")["ref"] == "TELCO-RATIONALISATION-DETECTORS@1.0"
    assert by_role(body, "scoringProfile")["ref"] == "TELCO-PORTFOLIO-SCORING@1.0"
    policy = by_role(body, "policyPack")
    assert policy["ref"] == "UK-CONSUMER-PORTFOLIO@1.0" and policy["source"] == "recommended"
    assert {o["ref"] for o in policy["options"]} == {"UK-CONSUMER-PORTFOLIO@1.0", "TELCO-DEFAULT-POLICY@1.0"}
    assert setting(body, "recommendedThreshold") | {} == {
        "name": "recommendedThreshold", "label": "Recommendation threshold", "value": 80,
        "source": "Scoring profile", "overridable": True, "allowed": None}
    assert setting(body, "minimumEvidenceCoverage")["value"] == 85
    assert setting(body, "missingEvidenceTreatment")["value"] == "NOT_ASSESSED"
    assert body["status"] == "BLOCKED" and [i["id"] for i in body["issues"] if i["severity"] == "BLOCKING"] == [
        "SNAPSHOT"]
    assert body["ai"]["profile_ref"] == "TELCO-AI-RUNTIME@1.0"
    r = env["client"].post(f"{R(env)}/start", headers=env["pm"], json={"version": body["version"]})
    assert r.status_code == 409 and r.json()["detail"]["code"] == "start_blocked"


def test_choose_pack_overrides_and_permissions(env):
    c = env["client"]
    body = cfg(env)
    r = c.put(f"{R(env)}/configuration", headers=env["pm"],
              json={"version": body["version"], "selections": {"policyPack": "TELCO-DEFAULT-POLICY@1.0"}})
    assert r.status_code == 200, r.text
    body = r.json()
    assert by_role(body, "policyPack")["source"] == "chosen" and setting(body, "minimumEvidenceCoverage")["value"] == 80
    stale = c.put(f"{R(env)}/configuration", headers=env["pm"], json={"version": 1, "selections": {}})
    assert stale.status_code == 409
    no_auth = c.put(f"{R(env)}/configuration", headers=env["pm"],
                    json={"version": body["version"], "overrides": {"recommendedThreshold": 90}})
    assert no_auth.status_code == 422
    assert "config.scoring" in no_auth.json()["detail"]["errors"][0]["message"]
    assert c.put(f"{R(env)}/configuration", headers=env["admin"],
                 json={"version": body["version"], "overrides": {}}).status_code == 403
    boss = two_role_user(env, ["portfolio_manager", "agent_admin"])
    r = c.put(f"{R(env)}/configuration", headers=boss,
              json={"version": body["version"], "overrides": {"recommendedThreshold": 90,
                                                              "humanReviewRequired": False}})
    assert r.status_code == 422  # human review is not overridable
    r = c.put(f"{R(env)}/configuration", headers=boss,
              json={"version": body["version"], "overrides": {"recommendedThreshold": 90}})
    assert r.status_code == 200 and setting(r.json(), "recommendedThreshold") == {
        "name": "recommendedThreshold", "label": "Recommendation threshold", "value": 90, "source": "Run override",
        "overridable": True, "allowed": None}
    bad = c.put(f"{R(env)}/configuration", headers=env["pm"],
                json={"version": r.json()["version"], "selections": {"scoringProfile": "NOPE@1.0"}})
    assert bad.status_code == 422
    assert c.get(f"{R(env)}/configuration", headers=env["viewer"]).status_code == 200


def test_acknowledge_start_and_manifest_is_pinned(env, db):
    c = env["client"]
    approved_snapshot(env)
    body = cfg(env)
    assert body["status"] == "WARNING" and body["snapshot"]["code"] == "SNAP-001"
    warnings = body["unacknowledged"]
    assert warnings and all(w.startswith("EVIDENCE:") for w in warnings)
    r = c.post(f"{R(env)}/start", headers=env["pm"], json={"version": body["version"]})
    assert r.status_code == 409 and r.json()["detail"]["message"] == "Acknowledge the evidence limitations first"
    assert c.post(f"{R(env)}/configuration/acknowledgements", headers=env["pm"],
                  json={"version": body["version"], "warning_ids": ["EVIDENCE:NOT_A_THING"]}).status_code == 422
    r = c.post(f"{R(env)}/configuration/acknowledgements", headers=env["pm"],
               json={"version": body["version"], "warning_ids": warnings})
    assert r.status_code == 200 and r.json()["unacknowledged"] == []
    assert c.post(f"{R(env)}/start", headers=env["steward"], json={"version": r.json()["version"]}).status_code == 403
    started = c.post(f"{R(env)}/start", headers=env["pm"], json={"version": r.json()["version"]})
    assert started.status_code == 201, started.text
    m = started.json()
    man = m["manifest"]
    roles = {a["role"]: a for a in man["artifacts"]}
    assert roles["scoringProfile"]["ref"] == "TELCO-PORTFOLIO-SCORING@1.0"
    assert roles["scoringProfile"]["digest"].startswith("sha256:")
    assert {"detectorPack", "priorityPolicy", "policyPack", "readinessPolicy", "precedencePolicy",
            "canonicalSchema"} <= set(roles)
    assert all("@" in a["ref"] for a in man["artifacts"])  # exact versions, never "latest"
    assert man["snapshot"]["code"] == "SNAP-001" and man["ai"]["profile"]["ref"] == "TELCO-AI-RUNTIME@1.0"
    assert {a["id"] for a in man["acknowledgements"]} == set(warnings)
    assert m["manifest_hash"].startswith("sha256:")

    # the run is locked
    run = c.get(f"{R(env)}", headers=env["pm"]).json()
    assert run["state"] == "ANALYSING"
    assert c.put(f"{R(env)}/configuration", headers=env["pm"],
                 json={"version": run["version"], "selections": {}}).status_code == 409

    # a newer scoring profile is published: the run keeps its pinned version
    with env["sf"]() as s:
        principal = _admin(env)
        art, v = registry.resolve(s, env["tenant"], "TELCO-PORTFOLIO-SCORING", kind=ArtifactKind.SCORING_PROFILE)
        _, d = registry.clone_version(s, env["platform"].audit, principal, ArtifactKind.SCORING_PROFILE, art.id,
                                      registry.version_of(v), Bump.MINOR, "new weights")
        content = {**v.content, "thresholds": {"recommended": 85, "review": 65}}
        _, d = registry.update_draft(s, env["platform"].audit, principal, ArtifactKind.SCORING_PROFILE, art.id,
                                     registry.version_of(d), {"content": content}, d.lock_version)
        registry.publish(s, env["platform"].audit, principal, ArtifactKind.SCORING_PROFILE, art.id,
                         registry.version_of(d), d.lock_version)
    again = cfg(env)
    assert again["started"] and by_role(again, "scoringProfile")["ref"] == "TELCO-PORTFOLIO-SCORING@1.0"
    assert setting(again, "recommendedThreshold")["value"] == 80 and again["manifest_hash"] == m["manifest_hash"]
    assert c.get(f"{R(env)}/manifest", headers=env["viewer"]).json()["manifest_hash"] == m["manifest_hash"]

    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE rationalisation.run_manifest SET manifest_hash = 'x'"))
    db.rollback()
    actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.tenant_id == env["tenant"])))
    assert {"run.limitations_acknowledged", "run.started"} <= actions
    events = set(db.scalars(select(OutboxEvent.event_type).where(OutboxEvent.tenant_id == env["tenant"])))
    assert {"RunStarted", "AnalysisRequested"} <= events


def test_manifest_404_and_tenancy(env, login):
    c = env["client"]
    assert c.get(f"{R(env)}/manifest", headers=env["pm"]).status_code == 404
    other = login("portfolio_manager", "t-other-config")
    assert c.get(f"{R(env)}/configuration", headers=other).status_code == 404


def _admin(env):
    from app.ports.auth import Principal
    return Principal(user_id="admin-test", tenant_id=env["tenant"], name="Admin", roles=("agent_admin",),
                     permissions=frozenset({"registry.manage", "registry.publish"}), auth_context={})
