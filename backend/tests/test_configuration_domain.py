"""Step 9: configuration content, deterministic pack recommendation, precedence, overrides, compatibility."""

import copy

from app.domain import configuration as cf
from app.domain.artifact_content import validate_content
from app.seed.configuration import CONFIGURATION_ITEMS, TELCO_SCORING, UK_CONSUMER_POLICY

CTX = cf.RunContext(markets=frozenset({"UK_CONSUMER"}), families=frozenset({"POSTPAID"}),
                    objectives=frozenset({"REDUCE_DUPLICATES", "BSS_SUNSET"}), canonical="TELCO-CANONICAL")


def cand(key, version=(1, 0), **applicability):
    a = {"markets": ["GLOBAL"], "productFamilies": ["ALL"], "objectives": ["REDUCE_DUPLICATES", "BSS_SUNSET"],
         "dimensions": ["COMMERCIAL", "TECHNICAL", "EXECUTION"], "priority": 10}
    a.update(applicability)
    return cf.Candidate(key, f"{key}@{version[0]}.{version[1]}", version, {"applicability": a})


def test_seed_content_valid():
    for kind, key, _, content in CONFIGURATION_ITEMS:
        assert validate_content(kind, content) == [], key


def test_content_checks():
    bad = copy.deepcopy(TELCO_SCORING)
    bad["weights"]["detectorSignal"] = 40
    bad["thresholds"] = {"recommended": 60, "review": 60}
    msgs = [e.message for e in validate_content("SCORING_PROFILE", bad)]
    assert "Weights total 105%, they must total 100%" in msgs
    assert "The recommended threshold must be above the review threshold" in msgs
    from app.seed.configuration import TELCO_DETECTORS, TELCO_PRIORITY
    det = copy.deepcopy(TELCO_DETECTORS)
    det["detectors"][0]["factors"].pop("lifecycle")
    det["detectors"][0]["thresholds"] = {"strong": 800, "probable": 850}
    det["primaryTypePrecedence"] = ["DUPLICATE"]
    msgs = [e.message for e in validate_content("DETECTOR_PACK", det)]
    assert any(m.startswith("DUPLICATE factors must be exactly") for m in msgs)
    assert "DUPLICATE thresholds need probable < strong" in msgs
    assert "List every detector of the pack exactly once" in msgs
    pr = copy.deepcopy(TELCO_PRIORITY)
    pr["bands"][2]["min"] = 30
    assert any("Bands must be" in e.message for e in validate_content("PRIORITY_POLICY", pr))
    pol = copy.deepcopy(UK_CONSUMER_POLICY)
    pol["gates"]["missingEvidenceTreatment"] = "ASSUME_ZERO"
    assert validate_content("POLICY_PACK", pol)  # prohibited value is not in the schema


def test_recommend_prefers_specific_then_priority_then_version():
    rec = cf.recommend([cand("GLOBAL-PACK", priority=90), cand("UK-PACK", markets=["UK_CONSUMER"], priority=10)],
                       CTX, "Policy pack")
    assert rec.chosen == "UK-PACK@1.0"  # specificity beats priority
    rec = cf.recommend([cand("A", priority=20), cand("B", priority=30)], CTX, "x")
    assert rec.chosen == "B@1.0"
    rec = cf.recommend([cand("A", (2, 1)), cand("A2", (2, 0))], CTX, "x")
    assert rec.chosen == "A@2.1"
    rec = cf.recommend([cand("FULL"), cand("HALF", objectives=["REDUCE_DUPLICATES"], priority=99)], CTX, "x")
    assert rec.chosen == "FULL@1.0" and rec.trace[-1].startswith("Recommended FULL@1.0: objectives 100%")


def test_recommend_filters_and_never_invents_a_preference():
    rec = cf.recommend([cand("IE", markets=["IE_CONSUMER"]), cand("PREPAID", productFamilies=["PREPAID"]),
                        cand("NOEXEC", dimensions=["COMMERCIAL", "TECHNICAL"]),
                        cand("OLDSCHEMA", canonicalSchema="OTHER-SCHEMA")], CTX, "Scoring profile")
    assert rec.chosen is None and rec.tie == []
    reasons = {r.ref: r.reasons for r in rec.ranking}
    assert reasons["IE@1.0"] == ["does not cover markets UK_CONSUMER"]
    assert reasons["PREPAID@1.0"] == ["does not cover product families POSTPAID"]
    assert reasons["NOEXEC@1.0"] == ["mandatory dimensions not covered: EXECUTION"]
    assert reasons["OLDSCHEMA@1.0"] == ["built for schema OTHER-SCHEMA, run uses TELCO-CANONICAL"]
    tie = cf.recommend([cand("X"), cand("Y")], CTX, "Detector pack")
    assert tie.chosen is None and tie.tie == ["X@1.0", "Y@1.0"] and "choose one" in tie.trace[-1]


def test_precedence_and_overrides():
    s = cf.resolve_settings(TELCO_SCORING, UK_CONSUMER_POLICY, {})
    assert s["recommendedThreshold"] == {"value": 80, "source": "Scoring profile"}
    assert s["minimumEvidenceCoverage"] == {"value": 85, "source": "Policy pack"}
    assert cf.resolve_settings(None, None, {})["reviewThreshold"] == {"value": 60, "source": "Telco default"}
    pol = {**UK_CONSUMER_POLICY, "thresholds": {"recommended": 85}}
    assert cf.resolve_settings(TELCO_SCORING, pol, {})["recommendedThreshold"]["source"] == "Policy pack"
    s = cf.resolve_settings(TELCO_SCORING, UK_CONSUMER_POLICY, {"recommendedThreshold": 90})
    assert s["recommendedThreshold"] == {"value": 90, "source": "Run override"}

    assert cf.check_overrides({"recommendedThreshold": 90}, UK_CONSUMER_POLICY, True) == []
    msgs = [e.message for e in cf.check_overrides({"recommendedThreshold": 90}, UK_CONSUMER_POLICY, False)]
    assert msgs == ["Only an authorised configurator (config.scoring) may override this"]
    msgs = [e.message for e in cf.check_overrides({"humanReviewRequired": False, "missingEvidenceTreatment":
                                                   "ASSUME_ZERO", "reviewThreshold": 101, "bogus": 1},
                                                  UK_CONSUMER_POLICY, True)]
    assert "The policy pack does not allow runs to change this setting" in msgs
    assert "ASSUME_ZERO is prohibited: missing evidence is never scored low" in msgs
    assert "Enter a whole number from 0 to 100" in msgs and "Unknown setting" in msgs


def test_compatibility_issues_and_status():
    sel = {r: {"ref": f"{r}@1.0", "eligible": True, "objective_coverage": 100}
           for r in ("detectorPack", "scoringProfile", "priorityPolicy", "policyPack")}
    settings = cf.resolve_settings(TELCO_SCORING, UK_CONSUMER_POLICY, {})
    readiness = {"domains": [
        {"domain": "CRM_CATALOG", "label": "CRM", "selected": True, "status": "AVAILABLE", "status_label": "Available",
         "coverage": 100, "consequence": "None"},
        {"domain": "INVENTORY", "label": "Inventory", "selected": True, "status": "NOT_ASSESSED",
         "status_label": "Missing", "coverage": 0, "consequence": "Footprint unknown."},
        {"domain": "CPQ", "label": "CPQ", "selected": False, "status": "NOT_ASSESSED", "status_label": "Missing",
         "coverage": 0, "consequence": "x"}]}
    ai = [{"task": "PRODUCT_MATCHING", "label": "Matching", "compliant": True, "chain": []}]
    issues = cf.compatibility(selections=sel, settings=settings, policy=UK_CONSUMER_POLICY,
                              snapshot={"code": "SNAP-001"}, readiness=readiness, ai_routes=ai)
    assert [(i.id, i.severity) for i in issues] == [("EVIDENCE:INVENTORY", "WARNING"), ("AI:NO_LLM", "INFO")]
    assert cf.status_of(issues) == "WARNING" and cf.warnings_to_acknowledge(issues) == ["EVIDENCE:INVENTORY"]

    sel2 = {**sel, "policyPack": {"ref": None, "tie": ["A@1.0", "B@1.0"]},
            "scoringProfile": {**sel["scoringProfile"], "objective_coverage": 50}}
    bad = cf.resolve_settings(TELCO_SCORING, UK_CONSUMER_POLICY, {"recommendedThreshold": 55,
                                                                  "humanReviewRequired": False})
    issues = cf.compatibility(selections=sel2, settings=bad, policy=UK_CONSUMER_POLICY, snapshot=None,
                              readiness=None, ai_routes=[{**ai[0], "compliant": False}])
    ids = {i.id: i.severity for i in issues}
    assert ids == {"SELECT:policyPack": "BLOCKING", "OBJECTIVES:scoringProfile": "WARNING",
                   "THRESHOLDS": "BLOCKING", "HUMAN_REVIEW": "BLOCKING", "SNAPSHOT": "BLOCKING",
                   "AI:PRODUCT_MATCHING": "BLOCKING", "AI:NO_LLM": "INFO"}
    assert "A@1.0 or B@1.0 rank equally" in next(i.message for i in issues if i.id == "SELECT:policyPack")
    assert cf.status_of(issues) == "BLOCKED"
