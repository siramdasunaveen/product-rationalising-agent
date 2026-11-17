"""Step 14 domain: decision validation, submission gating, approval route, policy checks, approval actions."""

from datetime import date

from app.domain import decisions as dm

TODAY = date(2026, 11, 17)  # a Tuesday
ELIGIBLE = {"PRD-V1": True, "PRD-V2": True, "PRD-OLD": False}
ALLOWED = ["RETAIN", "CONSOLIDATE", "STANDARDISE", "MIGRATE", "INVESTIGATE"]


def form(**over):
    base = {"disposition": "CONSOLIDATE", "targetProductId": "PRD-V2", "effectiveDate": date(2027, 3, 31),
            "ownerId": "a.patel", "rationale": "Consolidate V1 into V2 because the products are functionally "
            "equivalent and V1 drives elevated fallout."}
    base.update(over)
    return base


def fields(errors):
    return sorted({e.field for e in errors})


def test_validation_of_the_decision_form():
    assert dm.validate(form(), eligible_targets=ELIGIBLE, today=TODAY, allowed_actions=ALLOWED) == []
    assert fields(dm.validate(form(disposition="RETIRE", targetProductId=None), eligible_targets=ELIGIBLE, today=TODAY,
                              allowed_actions=ALLOWED)) == ["disposition"]
    assert fields(dm.validate(form(targetProductId=None), eligible_targets=ELIGIBLE, today=TODAY,
                              allowed_actions=ALLOWED)) == ["targetProductId"]
    assert fields(dm.validate(form(targetProductId="PRD-OLD"), eligible_targets=ELIGIBLE, today=TODAY,
                              allowed_actions=ALLOWED)) == ["targetProductId"]
    assert fields(dm.validate(form(disposition="RETAIN"), eligible_targets=ELIGIBLE, today=TODAY,
                              allowed_actions=ALLOWED)) == ["targetProductId"]  # retain takes no target
    assert fields(dm.validate(form(effectiveDate=date(2026, 11, 17)), eligible_targets=ELIGIBLE, today=TODAY,
                              allowed_actions=ALLOWED)) == ["effectiveDate"]
    assert fields(dm.validate(form(effectiveDate=date(2027, 4, 3)), eligible_targets=ELIGIBLE, today=TODAY,
                              allowed_actions=ALLOWED)) == ["effectiveDate"]  # a Saturday
    assert fields(dm.validate(form(rationale="too short", ownerId=" "), eligible_targets=ELIGIBLE, today=TODAY,
                              allowed_actions=ALLOWED)) == ["ownerId", "rationale"]


def test_content_hash_is_stable_and_sensitive():
    a = dm.content_hash({"disposition": "CONSOLIDATE", "rationale": "x", "versionNo": 1})
    assert a == dm.content_hash({"versionNo": 1, "rationale": "x", "disposition": "CONSOLIDATE"})
    assert a.startswith("sha256:") and a != dm.content_hash({"disposition": "MIGRATE", "rationale": "x", "versionNo": 1})


def test_submission_blockers_follow_readiness_and_execution_gate():
    ok = {"continueToDecisionEnabled": True, "gates": [{"key": "a", "status": "PASS", "reason": "ok"}]}
    dims = {"EXECUTION": {"status": "ASSESSED", "score": 70}}
    assert dm.submission_blockers(ok, {"executionReadinessGate": "REQUIRED_FOR_DECISION"}, dims, "CONSOLIDATE") == []
    blocked = {"continueToDecisionEnabled": False, "gates": [{"key": "preDecisionPolicy", "status": "FAIL",
                                                              "reason": "Consolidate needs evidence for: PROVISIONING"}]}
    assert dm.submission_blockers(blocked, {}, dims, "CONSOLIDATE") == ["Consolidate needs evidence for: PROVISIONING"]
    assert dm.submission_blockers(blocked, {}, dims, "INVESTIGATE") == []  # rule 5: investigate always submits
    gap = dm.submission_blockers(ok, {"executionReadinessGate": "REQUIRED_FOR_DECISION"},
                                 {"EXECUTION": {"status": "NOT_ASSESSED"}}, "MIGRATE")
    assert gap and "Execution readiness" in gap[0]
    assert dm.submission_blockers(None, {}, dims, "MIGRATE") == ["Decision readiness has not been evaluated for this disposition"]


POLICY = {"approvalRoutes": [
    {"dispositions": ["RETAIN", "INVESTIGATE"], "scopes": ["product"], "reason": "Low-impact outcome"},
    {"dispositions": ["SIMPLIFY"], "scopes": ["product", "commercial"], "reason": "Offer change"},
    {"dispositions": ["CONSOLIDATE", "MIGRATE", "RETIRE"], "scopes": ["product", "commercial", "architecture"],
     "reason": "Customer and technical impact"}]}
OVERRIDES = [{"kind": "CUSTOMER_IMPACT", "minimumBand": "HIGH", "minSubscriptions": 50000}]


def test_approval_route_from_policy_with_impact_escalation():
    r = dm.approval_route(POLICY, "CONSOLIDATE", {"activeSubscriptions": 86214}, OVERRIDES)
    assert r["scopes"] == ["product", "commercial", "architecture"] and r["reasons"] == ["Customer and technical impact"]
    low = dm.approval_route(POLICY, "RETAIN", {"activeSubscriptions": 120}, OVERRIDES)
    assert low["scopes"] == ["product"]
    big = dm.approval_route(POLICY, "RETAIN", {"activeSubscriptions": 86214}, OVERRIDES)
    assert big["scopes"] == ["product", "commercial"] and "86,214" in big["reasons"][1]
    unknown = dm.approval_route({"approvalRoutes": []}, "STANDARDISE", {}, [])
    assert unknown["scopes"] == ["product"]


def version(**over):
    base = {"versionNo": 3, "snapshotCode": "SNAP-005", "disposition": "CONSOLIDATE", "targetProductId": "PRD-V2",
            "recommendedAction": "CONSOLIDATE", "commercialTreatment": "Grandfather price until renewal",
            "conditions": ["PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL"]}
    base.update(over)
    return base


CAND = {"impact": {"activeSubscriptions": 86214, "protectedPriceContracts": 4209, "mrr": {"amount": 480000, "currency": "GBP"}},
        "dimensions": {"TECHNICAL": {"status": "ASSESSED", "score": 92}, "EXECUTION": {"status": "ASSESSED", "score": 70}}}
SETTINGS = {"technicalEquivalenceGate": "REQUIRED_FOR_CONSOLIDATE", "executionReadinessGate": "REQUIRED_FOR_DECISION"}
READY = {"gates": [{"key": "x", "status": "PASS", "reason": "ok"}]}


def test_policy_checks_cand_1042_pass_with_warning():
    checks = dm.policy_checks(version(), CAND, SETTINGS, READY, stale=False)
    by = {c["key"]: c for c in checks}
    assert by["commercialTreatment"]["status"] == "PASS" and by["commercialTreatment"]["detail"] == "Grandfather price until renewal"
    assert by["customerNotice"]["status"] == "WARNING" and by["customerNotice"]["detail"] == "30-day notice for 4,209 contracts"
    assert by["revenueImpact"]["status"] == "PASS" and by["architecture"]["status"] == "PASS"
    assert by["evidence"]["status"] == "PASS" and dm.overall(checks) == "PASS_WITH_WARNING"


def test_policy_checks_block_on_stale_missing_treatment_or_technical_gap():
    stale = dm.policy_checks(version(), CAND, SETTINGS, READY, stale=True)
    assert next(c for c in stale if c["key"] == "version")["status"] == "BLOCKED" and dm.overall(stale) == "BLOCKED"
    no_treat = dm.policy_checks(version(commercialTreatment=None), CAND, SETTINGS, READY, stale=False)
    assert next(c for c in no_treat if c["key"] == "commercialTreatment")["status"] == "BLOCKED"
    gap = dm.policy_checks(version(), {**CAND, "dimensions": {"TECHNICAL": {"status": "NOT_ASSESSED"}}}, SETTINGS,
                           READY, stale=False)
    assert next(c for c in gap if c["key"] == "architecture")["status"] == "BLOCKED"
    retain = dm.policy_checks(version(disposition="RETAIN", targetProductId=None, conditions=[], commercialTreatment=None),
                              {"impact": {}, "dimensions": {}}, SETTINGS, READY, stale=False)
    assert dm.overall(retain) == "PASS"
    approval_gate = dm.policy_checks(version(), {**CAND, "dimensions": {**CAND["dimensions"], "EXECUTION": {"status": "NOT_ASSESSED"}}},
                                     {**SETTINGS, "executionReadinessGate": "REQUIRED_FOR_APPROVAL"}, READY, stale=False)
    assert next(c for c in approval_gate if c["key"] == "executionReadiness")["status"] == "BLOCKED"


def test_validate_action_rules():
    ok = dict(scope_pending=True, has_scope=True, self_approval=False, allow_self_approval=False, blocked=False,
              expected_hash="sha256:a", actual_hash="sha256:a")
    assert dm.validate_action("APPROVE", None, **ok) == []
    assert dm.validate_action("REJECT", "", **ok) == ["Reject needs a comment"]
    assert dm.validate_action("HOLD", "  ", **ok) == ["Hold needs a comment"]
    assert dm.validate_action("RETURN", "please add dates", **ok) == []
    assert "Self-approval" in dm.validate_action("APPROVE", None, **{**ok, "self_approval": True})[0]
    assert dm.validate_action("REJECT", "no", **{**ok, "self_approval": True}) == []  # SoD limits approving only
    assert "no approval scope" in dm.validate_action("APPROVE", None, **{**ok, "has_scope": False})[0]
    assert "already acted" in dm.validate_action("APPROVE", None, **{**ok, "scope_pending": False})[0]
    assert "changed" in dm.validate_action("APPROVE", None, **{**ok, "expected_hash": "sha256:b"})[0]
    assert "BLOCKED" in dm.validate_action("APPROVE", None, **{**ok, "blocked": True})[0]
    assert dm.validate_action("RETURN", "fix", **{**ok, "blocked": True}) == []


def test_next_state_requires_every_scope():
    t = [{"scope": "product", "state": "APPROVED"}, {"scope": "commercial", "state": "PENDING"}]
    assert dm.next_state("SUBMITTED", "APPROVE", t) == "SUBMITTED"
    assert dm.next_state("SUBMITTED", "APPROVE", [{"scope": "product", "state": "APPROVED"}]) == "APPROVED"
    assert dm.next_state("SUBMITTED", "REJECT", t) == "REJECTED"
    assert dm.next_state("SUBMITTED", "RETURN", t) == "RETURNED"
    assert dm.next_state("SUBMITTED", "HOLD", t) == "ON_HOLD"


def test_material_change_detection():
    v = version()
    same = {"recommendation": {"action": "CONSOLIDATE", "targetProductId": "PRD-V2"}, "status": "REVIEW",
            "readinessForDecision": {"status": "PASS"}}
    assert dm.material_change(v, same) == []
    changed = {"recommendation": {"action": "MIGRATE", "targetProductId": "PRD-V2"}, "status": "REVIEW",
               "readinessForDecision": {"status": "BLOCKED"}}
    reasons = dm.material_change(v, changed)
    assert reasons[0].startswith("Recommendation changed from CONSOLIDATE to MIGRATE") and len(reasons) == 2
    gap = {"recommendation": {"action": "CONSOLIDATE", "targetProductId": "PRD-V2"}, "status": "EVIDENCE_GAP",
           "readinessForDecision": {"status": "PASS"}}
    assert dm.material_change(v, gap) == ["Candidate is now evidence gap"]


def test_default_effective_date_is_a_business_day():
    d = dm.default_effective_date(TODAY)
    assert d > TODAY and dm.is_business_day(d)
