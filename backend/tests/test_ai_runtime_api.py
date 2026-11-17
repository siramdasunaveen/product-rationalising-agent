"""Step 8 end to end: AI runtime screen API, routing through approved deployments, secrets, pinning, audit log."""

import json

import httpx
import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.adapters.local.ai import HttpContractProvider
from app.db.models import AIInvocation, AISecret
from app.domain.ai_runtime import Task
from app.ports.ai import AIRawResult, AIRequest, AIUnavailable
from app.services import ai_runtime as service
from app.services.artifact_seed import seed_artifacts

API = "/api/rationalisation/ai-runtime"
SCHEMA = {"type": "object", "required": ["ok"], "properties": {"ok": {"type": "boolean"}}}


class Fake:
    def __init__(self, answers, model="fake-model"):
        self.answers, self.model, self.requests = list(answers), model, []

    def complete_json(self, request):
        self.requests.append(request)
        a = self.answers.pop(0) if self.answers else AIUnavailable("no more answers")
        if isinstance(a, Exception):
            raise a
        return AIRawResult(text=json.dumps(a), model=self.model, provider="fake")


@pytest.fixture
def env(client, login, session_factory, tenant, platform, monkeypatch):
    with session_factory() as s:
        seed_artifacts(s, platform.audit, tenant)
    fakes: dict[str, Fake] = {}

    def fake_anthropic(dep, secret, plat):
        if not secret:
            raise LookupError("secret anthropic-api-key is not set")
        fakes.setdefault("secret", secret)
        return fakes["anthropic"]

    monkeypatch.setitem(service.BUILDERS, "anthropic", fake_anthropic)
    return {"client": client, "admin": login("agent_admin", tenant), "pm": login("portfolio_manager", tenant),
            "viewer": login("viewer", tenant), "tenant": tenant, "sf": session_factory, "platform": platform,
            "fakes": fakes}


def get(env, who="admin"):
    return env["client"].get(API, headers=env[who])


def test_default_runtime_is_platform_managed(env):
    r = get(env)
    assert r.status_code == 200, r.text
    body = r.json()
    assert (body["profile_ref"], body["strategy"], body["routing_policy"]) == (
        "TELCO-AI-RUNTIME@1.0", "PLATFORM_MANAGED", "TASK_SPECIFIC")
    assert [s["key"] for s in body["strategies"] if s["selected"]] == ["PLATFORM_MANAGED"]
    assert {d["key"] for d in body["deployments"]} == {"PLATFORM-DEFAULT", "ANTHROPIC-CLAUDE-OPUS"}
    assert len(body["routes"]) == 6 and all(rt["compliant"] for rt in body["routes"])
    assert {tuple(rt["chain"]) for rt in body["routes"]} == {("PLATFORM-DEFAULT@1.0",)}
    assert body["can_edit"] is True
    anthropic = next(d for d in body["deployments"] if d["key"] == "ANTHROPIC-CLAUDE-OPUS")
    assert anthropic["secret_set"] is False and anthropic["model"] == "claude-opus-5-5"
    assert get(env, "pm").json()["can_edit"] is False
    assert get(env, "viewer").status_code == 403


def test_switch_strategy_secret_and_route_through_registry_model(env):
    c, admin = env["client"], env["admin"]
    env["fakes"]["anthropic"] = Fake([{"ok": True}], model="claude-opus-5-5")
    routes = [{"task": t.value, "primary": "ANTHROPIC-CLAUDE-OPUS"} for t in Task]
    assert c.put(API, headers=env["pm"], json={"expected_ref": "TELCO-AI-RUNTIME@1.0",
                                                "strategy": "CUSTOMER_REGISTRY"}).status_code == 403
    r = c.put(API, headers=admin, json={"expected_ref": "TELCO-AI-RUNTIME@1.0", "strategy": "CUSTOMER_REGISTRY",
                                        "routes": routes})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["profile_ref"] == "TELCO-AI-RUNTIME@1.1" and body["strategy"] == "CUSTOMER_REGISTRY"
    assert all(rt["chain"] == ["ANTHROPIC-CLAUDE-OPUS@1.0"] for rt in body["routes"])
    cred = next(ch for ch in body["routes"][0]["checks"] if ch["key"] == "CREDENTIAL")
    assert cred["status"] == "WARNING" and body["routes"][0]["compliant"] is True
    stale = c.put(API, headers=admin, json={"expected_ref": "TELCO-AI-RUNTIME@1.0", "strategy": "NO_LLM"})
    assert stale.status_code == 409

    # no secret yet -> deterministic template, error recorded
    t = c.post(f"{API}/test/RECOMMENDATION_REASONING", headers=admin).json()
    assert t["source"] == "fallback" and any("anthropic-api-key is not set" in e for e in t["errors"])

    put = c.put(f"{API}/secrets/anthropic-api-key", headers=admin, json={"value": "sk-test-123"})
    assert put.status_code == 200 and "value" not in put.json()
    assert all(ch["key"] != "CREDENTIAL" for ch in get(env).json()["routes"][0]["checks"])
    assert [s["name"] for s in c.get(f"{API}/secrets", headers=admin).json()] == ["anthropic-api-key"]
    with env["sf"]() as s:
        row = s.scalar(select(AISecret).where(AISecret.tenant_id == env["tenant"]))
        assert row.ciphertext != "sk-test-123"
    t = c.post(f"{API}/test/RECOMMENDATION_REASONING", headers=admin).json()
    assert (t["source"], t["deployment"], t["model"]) == ("llm", "ANTHROPIC-CLAUDE-OPUS@1.0", "claude-opus-5-5")
    assert env["fakes"]["secret"] == "sk-test-123"

    inv = c.get(f"{API}/invocations", headers=admin).json()
    assert inv[0]["deployment_ref"] == "ANTHROPIC-CLAUDE-OPUS@1.0" and inv[0]["profile_ref"] == "TELCO-AI-RUNTIME@1.1"
    assert inv[1]["source"] == "fallback" and inv[1]["chain"] == []
    assert c.get(f"{API}/invocations", headers=env["pm"]).status_code == 403
    assert c.delete(f"{API}/secrets/anthropic-api-key", headers=admin).status_code == 204
    assert c.delete(f"{API}/secrets/anthropic-api-key", headers=admin).status_code == 404


def test_validation_of_changes(env):
    c, admin = env["client"], env["admin"]
    r = c.put(API, headers=admin, json={"expected_ref": "TELCO-AI-RUNTIME@1.0",
                                        "routes": [{"task": "PRODUCT_MATCHING", "primary": "NOT-THERE"}]})
    assert r.status_code == 422 and r.json()["detail"]["errors"][0]["field"] == "routes[0].primary"
    r = c.put(API, headers=admin, json={"expected_ref": "TELCO-AI-RUNTIME@1.0", "strategy": "NO_LLM"})
    assert r.status_code == 422  # NO_LLM strategy needs NO_LLM routing
    assert get(env).json()["profile_ref"] == "TELCO-AI-RUNTIME@1.0"  # nothing published, no draft left behind
    r = c.put(API, headers=admin, json={"expected_ref": "TELCO-AI-RUNTIME@1.0", "strategy": "NO_LLM",
                                        "routing_policy": "NO_LLM"})
    assert r.status_code == 200 and all(rt["chain"] == [] for rt in r.json()["routes"])
    bad_secret = c.put(f"{API}/secrets/Bad_Name", headers=admin, json={"value": "x"})
    assert bad_secret.status_code == 422


def test_publishing_a_route_without_eligible_model_fails_closed(env):
    c, admin = env["client"], env["admin"]
    # customer registry strategy, but every route still points at the platform model
    r = c.put(API, headers=admin, json={"expected_ref": "TELCO-AI-RUNTIME@1.0", "strategy": "CUSTOMER_REGISTRY"})
    assert r.status_code == 422
    errs = r.json()["detail"]["errors"]
    assert len(errs) == 6 and errs[0]["field"] == "routes.MAPPING_SUGGESTION"
    assert "does not fit strategy CUSTOMER_REGISTRY" in errs[0]["message"]
    # deterministic routes are always allowed
    routes = [{"task": t.value, "mode": "DETERMINISTIC"} for t in Task]
    r = c.put(API, headers=admin, json={"expected_ref": "TELCO-AI-RUNTIME@1.0", "strategy": "CUSTOMER_REGISTRY",
                                        "routes": routes})
    assert r.status_code == 200 and all(x["compliant"] and x["chain"] == [] for x in r.json()["routes"])


def test_chain_falls_back_to_second_model_then_template(env):
    platform = env["platform"]
    platform.ai = Fake([AIUnavailable("platform down")])
    env["fakes"]["anthropic"] = Fake([{"ok": "not a boolean"}, {"ok": True}])
    with env["sf"]() as s:
        service.set_secret(s, platform, _principal(env), "anthropic-api-key", "k")
    c = env["client"]
    routes = [{"task": "PRODUCT_MATCHING", "primary": "PLATFORM-DEFAULT", "fallback": "ANTHROPIC-CLAUDE-OPUS"}]
    r = c.put(API, headers=env["admin"], json={"expected_ref": "TELCO-AI-RUNTIME@1.0", "strategy": "PLATFORM_MANAGED",
                                               "routing_policy": "TASK_SPECIFIC", "routes": routes})
    route = next(x for x in r.json()["routes"] if x["task"] == "PRODUCT_MATCHING")
    # the registry model is not platform-hosted, so platform-managed strategy keeps only the platform model
    assert route["chain"] == ["PLATFORM-DEFAULT@1.0"] and "ANTHROPIC-CLAUDE-OPUS" in route["rejected"]

    # with the customer registry strategy both are eligible? no: the platform model is not registry-hosted
    r = c.put(API, headers=env["admin"], json={"expected_ref": "TELCO-AI-RUNTIME@1.1",
                                               "strategy": "CUSTOMER_REGISTRY", "routes": [
                                                   {"task": t.value, "primary": "ANTHROPIC-CLAUDE-OPUS"}
                                                   for t in Task]})
    assert r.status_code == 200, r.text
    with env["sf"]() as s:
        res = service.run_task(s, platform, env["tenant"], "u1", Task.PRODUCT_MATCHING,
                               AIRequest(task="x", system="s", prompt="p", schema=SCHEMA),
                               fallback=lambda: {"ok": False})
        s.commit()
    # first answer fails the schema, the retry is accepted on the same model
    assert (res.source, res.attempts, res.deployment) == ("llm", 2, "ANTHROPIC-CLAUDE-OPUS@1.0")
    assert any("ok: 'not a boolean' is not of type 'boolean'" in e for e in res.errors)


def test_two_model_chain_and_human_review(env):
    platform = env["platform"]
    with env["sf"]() as s:
        service.set_secret(s, platform, _principal(env), "anthropic-api-key", "k")
        # a second registry model, published like any other artifact
        from app.domain.artifacts import ArtifactKind
        from app.services import artifacts as registry
        p = _principal(env)
        a, v = registry.create_artifact(s, platform.audit, p, ArtifactKind.AI_DEPLOYMENT, key="CUSTOMER-SECOND",
                                        name="Second", content={
                                            "provider": "anthropic", "model": "claude-sonnet-5-5",
                                            "displayName": "Second", "hosting": "CUSTOMER_REGISTRY", "region": "US",
                                            "secretRef": "anthropic-api-key",
                                            "dataClassifications": ["AGGREGATES"],
                                            "capabilities": {"structuredOutput": True}})
        registry.publish(s, platform.audit, p, ArtifactKind.AI_DEPLOYMENT, a.id, registry.version_of(v),
                         v.lock_version)
    env["fakes"]["anthropic"] = Fake([AIUnavailable("first down"), {"ok": True}])
    c = env["client"]
    r = c.put(API, headers=env["admin"], json={
        "expected_ref": "TELCO-AI-RUNTIME@1.0", "strategy": "CUSTOMER_REGISTRY", "failure_treatment": "HUMAN_REVIEW",
        "routes": [{"task": t.value, "primary": "ANTHROPIC-CLAUDE-OPUS", "fallback": "CUSTOMER-SECOND"}
                   for t in Task]})
    route = next(x for x in r.json()["routes"] if x["task"] == "DECISION_RATIONALE")
    assert route["chain"] == ["ANTHROPIC-CLAUDE-OPUS@1.0", "CUSTOMER-SECOND@1.0"]
    with env["sf"]() as s:
        res = service.run_task(s, platform, env["tenant"], "u1", Task.DECISION_RATIONALE,
                               AIRequest(task="x", system="s", prompt="p", schema=SCHEMA), fallback=lambda: {"ok": False})
        assert (res.source, res.deployment) == ("llm", "CUSTOMER-SECOND@1.0")
        env["fakes"]["anthropic"].answers = [AIUnavailable("a"), AIUnavailable("b")]
        res = service.run_task(s, platform, env["tenant"], "u1", Task.DECISION_RATIONALE,
                               AIRequest(task="x", system="s", prompt="p", schema=SCHEMA), fallback=lambda: {"ok": False})
        assert res.source == "fallback" and res.needs_human_review is True and res.output == {"ok": False}
        s.commit()


def test_pinned_route_survives_profile_change(env):
    platform = env["platform"]
    platform.ai = Fake([{"ok": True}], model="platform-model")
    with env["sf"]() as s:
        pins = service.pin(s, env["tenant"])
    assert pins["profile"]["ref"] == "TELCO-AI-RUNTIME@1.0"
    assert pins["routes"]["PRODUCT_MATCHING"][0]["ref"] == "PLATFORM-DEFAULT@1.0"
    env["client"].put(API, headers=env["admin"], json={"expected_ref": "TELCO-AI-RUNTIME@1.0", "strategy": "NO_LLM",
                                                       "routing_policy": "NO_LLM"})
    with env["sf"]() as s:
        live = service.run_task(s, platform, env["tenant"], "u1", Task.PRODUCT_MATCHING,
                                AIRequest(task="x", system="s", prompt="p", schema=SCHEMA), fallback=lambda: {"ok": False})
        pinned = service.run_task(s, platform, env["tenant"], "u1", Task.PRODUCT_MATCHING,
                                  AIRequest(task="x", system="s", prompt="p", schema=SCHEMA),
                                  fallback=lambda: {"ok": False}, pinned=pins)
        s.commit()
    assert live.source == "fallback" and live.deployment is None  # live profile is now no-LLM
    assert (pinned.source, pinned.deployment, pinned.model) == ("llm", "PLATFORM-DEFAULT@1.0", "platform-model")


def test_invocations_are_append_only(env, db):
    with env["sf"]() as s:
        service.run_task(s, env["platform"], env["tenant"], "u1", Task.PRODUCT_MATCHING,
                         AIRequest(task="x", system="s", prompt="p", schema=SCHEMA), fallback=lambda: {"ok": False})
        s.commit()
    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE rationalisation.ai_invocation SET source = 'llm'"))
    db.rollback()
    assert db.scalar(select(AIInvocation.source).where(AIInvocation.tenant_id == env["tenant"])) == "fallback"


def test_byo_http_contract():
    seen = {}

    def handler(request: httpx.Request) -> httpx.Response:
        seen["auth"] = request.headers.get("authorization")
        seen["body"] = json.loads(request.content)
        return httpx.Response(200, json={"output": {"ok": True}, "model": "acme-llm-7"})

    p = HttpContractProvider("https://ai.acme.example/run", "tok", "acme", transport=httpx.MockTransport(handler))
    raw = p.complete_json(AIRequest(task="PRODUCT_MATCHING", system="s", prompt="p", schema=SCHEMA))
    assert json.loads(raw.text) == {"ok": True} and raw.model == "acme-llm-7"
    assert seen["auth"] == "Bearer tok" and seen["body"]["schema"] == SCHEMA and seen["body"]["model"] == "acme"
    for resp in (httpx.Response(500), httpx.Response(200, text="nope"), httpx.Response(200, json={"x": 1})):
        bad = HttpContractProvider("https://x.example", None, transport=httpx.MockTransport(lambda r, resp=resp: resp))
        with pytest.raises(AIUnavailable):
            bad.complete_json(AIRequest(task="t", system="s", prompt="p", schema=SCHEMA))


def _principal(env):
    from app.ports.auth import Principal
    return Principal(user_id="admin-test", tenant_id=env["tenant"], name="Admin", roles=("agent_admin",),
                     permissions=frozenset({"config.ai", "registry.manage", "registry.publish"}), auth_context={})
