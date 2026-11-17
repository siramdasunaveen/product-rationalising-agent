"""Step 12 domain: decision-readiness gates and Candidate Explorer views (pure)."""

from app.domain import candidate_views as cv
from app.domain import decision_readiness as dr

SETTINGS = {"minimumEvidenceCoverage": 80, "technicalEquivalenceGate": "REQUIRED_FOR_CONSOLIDATE",
            "staleEvidenceTreatment": "BLOCK"}
DISPOSITIONS = {"CONSOLIDATE": ["TECHNICAL_CATALOG", "PROVISIONING"],
                "MIGRATE": ["SERVICE_INVENTORY"], "RETIRE": ["SERVICE_INVENTORY"], "RETAIN": [], "INVESTIGATE": []}


def facts(**over):
    base = {
        "coverage": {"PRD-V1": ["PRODUCT_CATALOG", "TECHNICAL_CATALOG", "PROVISIONING", "SERVICE_INVENTORY"],
                     "PRD-V2": ["PRODUCT_CATALOG", "TECHNICAL_CATALOG", "PROVISIONING", "SERVICE_INVENTORY"]},
        "staleDomains": [],
        "dimensions": {"COMMERCIAL": {"status": "ASSESSED", "score": 90, "coverage": 100},
                       "TECHNICAL": {"status": "ASSESSED", "score": 92, "coverage": 100},
                       "EXECUTION": {"status": "ASSESSED", "score": 70, "coverage": 100}},
        "footprint": {"PRD-V1": True, "PRD-V2": True},
        "eligible": {"PRD-V1": True, "PRD-V2": True},
        "sourceProductId": "PRD-V1", "targetProductId": "PRD-V2", "techPass": 80,
    }
    base.update(over)
    return base


def ev(action, target="PRD-V2", f=None, blockers=(), settings=SETTINGS, primary="DUPLICATE"):
    return dr.evaluate(action, target, primary, [], f or facts(), settings, DISPOSITIONS, list(blockers),
                       ["PRD-V1", "PRD-V2"])


def gate(result, key):
    return next(g for g in result["gates"] if g["key"] == key)


def test_happy_path_consolidate_passes_all_seven_gates():
    r = ev("CONSOLIDATE")
    assert [g["key"] for g in r["gates"]] == list(dr.GATES)
    assert r["status"] == "PASS" and r["continueToDecisionEnabled"] is True


def test_rule_3_retire_with_active_footprint_is_not_permitted():
    r = ev("RETIRE", None, facts(footprint={"PRD-V1": True}), primary="LEGACY")
    assert gate(r, "actionPermitted")["status"] == "FAIL" and "rule 3" in gate(r, "actionPermitted")["reason"]
    unknown = ev("RETIRE", None, facts(footprint={}), primary="LEGACY")
    assert ev("RETIRE", None, facts(footprint={"PRD-V1": False}), primary="LEGACY")["status"] == "PASS"
    assert "unknown" in gate(unknown, "actionPermitted")["reason"]


def test_rule_4_technically_different_blocks_consolidate_but_not_migrate():
    f = facts(dimensions={**facts()["dimensions"], "TECHNICAL": {"status": "ASSESSED", "score": 55, "coverage": 100}})
    assert "rule 4" in gate(ev("CONSOLIDATE", f=f), "actionPermitted")["reason"]
    assert ev("MIGRATE", f=f)["status"] == "PASS"


def test_technical_not_assessed_is_never_a_fail_only_not_assessed():
    f = facts(dimensions={**facts()["dimensions"], "TECHNICAL": {"status": "NOT_ASSESSED", "score": None,
                                                                 "coverage": 0}})
    r = ev("CONSOLIDATE", f=f)
    assert gate(r, "technicalEquivalenceGate")["status"] == "NOT_ASSESSED"
    assert gate(r, "minimumEvidenceCoverage")["status"] == "FAIL"
    assert r["status"] == "BLOCKED" and r["continueToDecisionEnabled"] is False


def test_rule_6_missing_disposition_domain_blocks_pre_decision_policy():
    f = facts(coverage={"PRD-V1": ["PRODUCT_CATALOG", "TECHNICAL_CATALOG"], "PRD-V2": ["PRODUCT_CATALOG"]})
    r = ev("CONSOLIDATE", f=f)
    assert gate(r, "preDecisionPolicy")["status"] == "FAIL"
    assert "PROVISIONING" in gate(r, "preDecisionPolicy")["reason"]


def test_target_gate_and_inactive_target():
    assert gate(ev("CONSOLIDATE", None), "targetEligibility")["status"] == "FAIL"
    assert gate(ev("CONSOLIDATE", "PRD-X"), "targetEligibility")["status"] == "FAIL"
    assert gate(ev("CONSOLIDATE", f=facts(eligible={"PRD-V1": True, "PRD-V2": False})),
                "targetEligibility")["status"] == "FAIL"
    assert gate(ev("RETAIN", None), "targetEligibility")["status"] == "PASS"


def test_blockers_and_freshness():
    conflict = {"category": "VALIDATION", "consequence": "BLOCKED", "message": "conflict"}
    assert gate(ev("CONSOLIDATE", blockers=[conflict]), "criticalConflicts")["status"] == "FAIL"
    dep = {"category": "DEPENDENCY", "consequence": "BLOCKED", "message": "Billing dependency"}
    assert gate(ev("CONSOLIDATE", blockers=[dep]), "preDecisionPolicy")["reason"] == "Billing dependency"
    stale = facts(staleDomains=["PROVISIONING"])
    assert gate(ev("CONSOLIDATE", f=stale), "freshness")["status"] == "FAIL"
    warn = {**SETTINGS, "staleEvidenceTreatment": "ALLOW_WITH_WARNING"}
    assert gate(ev("CONSOLIDATE", f=stale, settings=warn), "freshness")["status"] == "PASS"


def test_investigate_always_continues_even_when_blocked():
    r = ev("INVESTIGATE", None, facts(coverage={}))
    assert r["continueToDecisionEnabled"] is True


def test_allowed_actions_follow_type_order():
    assert dr.allowed_actions("LEGACY", ["OPERATIONAL_BURDEN"]) == ["RETAIN", "SIMPLIFY", "MIGRATE", "RETIRE",
                                                                   "INVESTIGATE"]
    assert "CONSOLIDATE" not in dr.allowed_actions("LEGACY", [])


# ---------------------------------------------------------------------------
# Views
# ---------------------------------------------------------------------------

def item(code, **over):
    base = {"candidateId": code, "products": ["Fibre 100"], "productIds": ["PRD-1"], "primaryType": "DUPLICATE",
            "supportingTypes": [], "score": 80, "effectivePriority": "HIGH", "recommendation": "MIGRATE",
            "status": "REVIEW", "hidden": False, "decisionReadiness": "PASS"}
    base.update(over)
    return base


ITEMS = [item("CAND-0001"), item("CAND-0002", effectivePriority="CRITICAL", score=70),
         item("CAND-0003", effectivePriority="LOW", status="BLOCKED", decisionReadiness="BLOCKED"),
         item("CAND-0004", hidden=True, score=40, effectivePriority=None, recommendation=None),
         item("CAND-0005", primaryType="OVERLAP", supportingTypes=["LEGACY"], status="EVIDENCE_GAP",
              products=["Voice Legacy"], productIds=["PRD-9"])]


def test_builtin_views_have_declared_filters_and_valid_shapes():
    assert len(cv.BUILTIN_VIEWS) == 9
    for v in cv.BUILTIN_VIEWS:
        assert cv.validate_filters(v.filters) == [] and cv.validate_sort(v.sort) == []
    assert cv.VIEWS_BY_ID["VIEW-READY-FOR-DECISION"].filters == {"status": ["REVIEW"], "decisionReadiness": ["PASS"]}


def test_effective_filters_reported_and_hidden_excluded_by_default():
    out = cv.apply(ITEMS, cv.VIEWS_BY_ID["VIEW-ALL"], {}, None, 1, 50)
    assert out["inventoryTotal"] == 5 and out["filteredCount"] == 4
    assert out["effectiveFilters"] == {"includeHidden": False}
    shown = cv.apply(ITEMS, None, {"includeHidden": True}, None, 1, 50)
    assert shown["filteredCount"] == 5


def test_explicit_filters_replace_view_filters_and_type_matches_supporting():
    out = cv.apply(ITEMS, cv.VIEWS_BY_ID["VIEW-HIGH-PRIORITY"], {"priority": ["LOW"]}, None, 1, 50)
    assert out["effectiveFilters"]["priority"] == ["LOW"] and out["filteredCount"] == 1
    legacy = cv.apply(ITEMS, cv.VIEWS_BY_ID["VIEW-LEGACY"], {}, None, 1, 50)
    assert [i["candidateId"] for i in legacy["items"]] == ["CAND-0005"]
    q = cv.apply(ITEMS, None, {"query": "voice"}, None, 1, 50)
    assert [i["candidateId"] for i in q["items"]] == ["CAND-0005"]


def test_sort_is_severity_ordered_never_alphabetical_and_paged():
    out = cv.apply(ITEMS, None, {"includeHidden": True}, None, 1, 2)
    assert [i["candidateId"] for i in out["items"]] == ["CAND-0002", "CAND-0001"]  # CRITICAL before HIGH
    p3 = cv.apply(ITEMS, None, {"includeHidden": True}, None, 3, 2)
    assert [i["candidateId"] for i in p3["items"]] == ["CAND-0004"]  # unassessed last
    by_score = cv.apply(ITEMS, None, {}, [{"field": "score", "direction": "ASC"}], 1, 50)
    assert [i["score"] for i in by_score["items"]] == [70, 80, 80, 80]


def test_validation_rejects_unknown_values():
    assert cv.validate_filters({"priority": ["URGENT"]}) == ["priority: unknown value(s) URGENT"]
    assert cv.validate_filters({"minScore": 101}) and cv.validate_filters({"bogus": 1})
    assert cv.validate_sort([{"field": "colour", "direction": "ASC"}])
