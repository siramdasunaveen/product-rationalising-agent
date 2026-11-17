"""Step 8: AI runtime planning (pure). Strategies, routing policies, governance checks, max 2 models, pinning."""

import copy
from datetime import date

from app.domain.ai_runtime import Deployment, Task, pinned_routes, plan_all, plan_route
from app.domain.artifact_content import validate_content
from app.seed.ai import AI_DEPLOYMENTS, TELCO_AI_RUNTIME

TODAY = date(2026, 11, 17)


def dep(key, *, status="PUBLISHED", **over):
    content = {"provider": "anthropic", "model": "m", "displayName": key, "hosting": "CUSTOMER_REGISTRY",
               "region": "UK", "dataClassifications": ["METADATA", "AGGREGATES"],
               "capabilities": {"structuredOutput": True}, "qualityTier": 3, "costTier": 3}
    content.update(over)
    d = Deployment(key=key, ref=f"{key}@1.0", digest="sha256:" + key, status=status, content=content)
    return d


def reg(*deps):
    out = {}
    for d in deps:
        out[d.key] = out[d.ref] = d
    return out


def profile(**over):
    p = {"strategy": "CUSTOMER_REGISTRY", "routingPolicy": "TASK_SPECIFIC", "failureTreatment": "DETERMINISTIC_FALLBACK",
         "constraints": {"dataClassification": "AGGREGATES"}, "routes": []}
    p.update(over)
    return p


def test_seed_content_is_valid():
    for key, _, content in AI_DEPLOYMENTS:
        assert validate_content("AI_DEPLOYMENT", content) == [], key
    assert validate_content("AI_RUNTIME_PROFILE", TELCO_AI_RUNTIME) == []


def test_content_checks():
    bad = {"provider": "byo_http", "displayName": "x", "hosting": "PLATFORM", "region": "UK", "endpoint": "ftp://x",
           "dataClassifications": ["AGGREGATES"], "capabilities": {"structuredOutput": True}}
    msgs = [e.message for e in validate_content("AI_DEPLOYMENT", bad)]
    assert "Provider byo_http needs a model" in msgs
    assert "A bring-your-own endpoint is CUSTOMER_HOSTED" in msgs
    assert any("https://" in m for m in msgs)
    ok = {**bad, "model": "x", "hosting": "CUSTOMER_HOSTED", "endpoint": "http://localhost:9000/ai"}
    assert validate_content("AI_DEPLOYMENT", ok) == []
    p = profile(strategy="NO_LLM", routes=[{"task": "PRODUCT_MATCHING", "primary": "AA", "fallback": "AA"},
                                           {"task": "PRODUCT_MATCHING"}])
    msgs = [e.message for e in validate_content("AI_RUNTIME_PROFILE", p)]
    assert "NO_LLM strategy and NO_LLM routing go together" in msgs
    assert "The fallback must be a different deployment" in msgs and "PRODUCT_MATCHING is routed twice" in msgs


def test_task_specific_route_with_fallback():
    p = profile(routes=[{"task": "RECOMMENDATION_REASONING", "primary": "A", "fallback": "B"}])
    plan = plan_route(p, "RECOMMENDATION_REASONING", reg(dep("A"), dep("B"), dep("C")), TODAY)
    assert [d.ref for d in plan.chain] == ["A@1.0", "B@1.0"] and plan.compliant
    assert {c.key: c.status for c in plan.checks} == {"APPROVED": "PASS", "STRUCTURED_OUTPUT": "PASS",
                                                      "CITATIONS": "PASS", "RESIDENCY": "PASS", "FALLBACK": "PASS"}
    assert plan.trace[-1] == "Chosen: A@1.0, B@1.0 → deterministic template"


def test_governance_rejects_and_falls_through():
    deps = reg(dep("OLD", status="DEPRECATED"), dep("EU", region="EU"), dep("NOJSON", capabilities={"structuredOutput": False}),
               dep("PUBLIC", dataClassifications=["METADATA"]), dep("EXPIRED", expiresOn="2026-01-01"),
               dep("TOOLS", capabilities={"structuredOutput": True, "toolUse": True}), dep("PLAT", provider="platform",
                                                                                         hosting="PLATFORM"))
    p = profile(constraints={"dataClassification": "AGGREGATES", "allowedRegions": ["UK"]}, routingPolicy="QUALITY_FIRST")
    plan = plan_route(p, "PRODUCT_MATCHING", deps, TODAY)
    assert plan.chain == [] and not plan.compliant
    r = plan.rejected
    assert r["OLD"] == ["not approved (deprecated)"]
    assert r["EU"] == ["region EU is not allowed (UK)"]
    assert r["NOJSON"] == ["no structured (JSON schema) output"]
    assert r["PUBLIC"] == ["not approved for aggregates data"]
    assert r["EXPIRED"] == ["approval expired on 2026-01-01"]
    assert r["TOOLS"] == ["tool use is enabled but not permitted"]
    assert "does not fit strategy CUSTOMER_REGISTRY" in r["PLAT"][0]
    approved = next(c for c in plan.checks if c.key == "APPROVED")
    assert approved.status == "FAIL" and "No approved, eligible deployment" in approved.message


def test_max_two_models_and_ranking():
    deps = reg(dep("CHEAP", qualityTier=2, costTier=1), dep("BEST", qualityTier=5, costTier=5),
               dep("MID", qualityTier=4, costTier=2))
    quality = plan_route(profile(routingPolicy="QUALITY_FIRST"), "TECHNICAL_EXPLANATION", deps, TODAY)
    cost = plan_route(profile(routingPolicy="COST_OPTIMISED"), "TECHNICAL_EXPLANATION", deps, TODAY)
    assert [d.key for d in quality.chain] == ["BEST", "MID"]
    assert [d.key for d in cost.chain] == ["CHEAP", "MID"]
    budget = plan_route(profile(routingPolicy="QUALITY_FIRST", constraints={"dataClassification": "AGGREGATES",
                                                                            "maxCostTier": 2}),
                        "TECHNICAL_EXPLANATION", deps, TODAY)
    assert [d.key for d in budget.chain] == ["MID", "CHEAP"]
    assert budget.rejected["BEST"] == ["cost tier 5 above the budget (2)"]


def test_no_llm_and_deterministic_routes():
    p = profile(strategy="NO_LLM", routingPolicy="NO_LLM")
    plans = plan_all(p, reg(dep("A")), TODAY)
    assert all(pl.deterministic_only and pl.compliant for pl in plans)
    assert plans[0].trace == ["No-LLM mode: rules, scoring and templates only"]
    p = profile(routes=[{"task": "DECISION_RATIONALE", "mode": "DETERMINISTIC"}])
    pl = plan_route(p, "DECISION_RATIONALE", reg(dep("A")), TODAY)
    assert pl.chain == [] and pl.compliant and pl.checks[0].message == "Deterministic execution"


def test_customer_hosted_only_and_strategy_hosting():
    byo = dep("BYO", provider="byo_http", hosting="CUSTOMER_HOSTED", endpoint="https://ai.example.com/run")
    p = profile(strategy="BYO_ENDPOINT", routingPolicy="CUSTOMER_HOSTED_ONLY")
    plan = plan_route(p, "PRODUCT_MATCHING", reg(byo, dep("REG")), TODAY)
    assert [d.key for d in plan.chain] == ["BYO"]
    assert "does not fit strategy BYO_ENDPOINT" in plan.rejected["REG"][0]


def test_alternate_model_warning_and_unknown_route_target():
    p = profile(failureTreatment="ALTERNATE_APPROVED_MODEL",
                routes=[{"task": "TRANSITION_NARRATIVE", "primary": "A", "fallback": "GONE"}])
    plan = plan_route(p, "TRANSITION_NARRATIVE", reg(dep("A")), TODAY)
    assert [d.key for d in plan.chain] == ["A"] and plan.rejected == {"GONE": ["no such deployment in the registry"]}
    fb = next(c for c in plan.checks if c.key == "FALLBACK")
    assert fb.status == "WARNING" and plan.compliant


def test_pinned_routes_are_exact_refs():
    p = copy.deepcopy(TELCO_AI_RUNTIME)
    platform_dep = dep("PLATFORM-DEFAULT", provider="platform", hosting="PLATFORM")
    pins = pinned_routes(plan_all(p, reg(platform_dep), TODAY))
    assert set(pins) == {t.value for t in Task}
    assert pins["MAPPING_SUGGESTION"] == [{"ref": "PLATFORM-DEFAULT@1.0", "digest": "sha256:PLATFORM-DEFAULT"}]
