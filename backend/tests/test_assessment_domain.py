"""Step 11: assessment (pure). Opportunity Score (CAND-1042 = 92, rounding, missing inputs), dimensions, guardrail,
recommendation guards (CLAUDE.md rules 3-5), conditions, impact, priority bands/overrides, blockers, status."""

from datetime import date, timedelta

import pytest

from app.domain import assessment as am
from app.domain.detectors import ProductFeatures
from app.seed.configuration import TELCO_PRIORITY, TELCO_SCORING
from app.seed.telco import TELCO_READINESS

AS_OF = date(2026, 11, 17)
ALL_DOMAINS = {"CRM_CATALOG", "CPQ", "PRICE_BOOK", "LIFECYCLE", "TECHNICAL_CATALOG", "PROVISIONING", "INVENTORY",
               "ORDER_BILLING_ASSURANCE", "CUSTOMER_REVENUE_AGGREGATES"}
SETTINGS = {"recommendedThreshold": 80, "reviewThreshold": 60, "belowReviewTreatment": "NO_RECOMMENDATION",
            "minimumEvidenceCoverage": 80, "missingEvidenceTreatment": "NOT_ASSESSED",
            "criticalConflictTreatment": "BLOCK", "staleEvidenceTreatment": "REQUIRE_REFRESH",
            "technicalEquivalenceGate": "REQUIRED_FOR_CONSOLIDATE", "executionReadinessGate": "REQUIRED_FOR_DECISION",
            "humanReviewRequired": True}


def prod(pid, **kw):
    base = dict(name=pid, family="POSTPAID", status="ACTIVE", segment="Residential", market="UK Consumer",
                channel="Online", lifecycle="ACTIVE", cpq_options={"OPT-1"}, prices={"GBP": 15.0},
                services={"CFS-5G"}, service_types={"mobile data"}, resources={"RES-5G"},
                workflows=[{"id": "WF-1", "legacy": False, "compatibility": "COMPATIBLE"}],
                inventory_active=1000, inventory_legacy=False, subscriptions=20000, protected_contracts=0,
                mrr=100000.0, currency="GBP", billing_systems={"BILL-A"}, legacy_billing=False,
                orders=1000, fallout=30)
    base.update(kw)
    return ProductFeatures(product_id=pid, **base)


def ctx(products, coverage=None, **kw):
    c = am.Context(
        products={p.product_id: p for p in products},
        coverage=coverage or {p.product_id: set(ALL_DOMAINS) for p in products},
        stale_domains=set(), conflicted={}, objectives={"REDUCE_DUPLICATES", "BSS_SUNSET"},
        settings=dict(SETTINGS), scoring=TELCO_SCORING, priority=TELCO_PRIORITY,
        dispositions=TELCO_READINESS["dispositions"],
        domain_importance={d["domain"]: d["importance"] for d in TELCO_READINESS["domains"]},
        domain_labels={}, as_of=AS_OF, family_fallout={"POSTPAID": 0.035})
    for k, v in kw.items():
        setattr(c, k, v)
    return c


def cand(subjects, primary, signal, supporting=()):
    return am.CandidateInput("CAND-0001", list(subjects), primary, list(supporting),
                             [{"type": primary, "signal": signal, "factors": {}, "evidence_ids": []}])


def test_cand_1042_opportunity_score_is_92():
    out = am.opportunity_score({"detectorSignal": .94, "commercialValue": .90, "technicalFeasibility": .92,
                                "operationalBenefit": .86, "strategicAlignment": .98}, TELCO_SCORING["weights"], 60)
    assert out["score"] == 92 and out["coverage"] == 100
    assert out["inputs"]["detectorSignal"] == {"value": 940, "weight": 35, "contribution": 329}
    # unrounded signal from the guide (.9415) also gives 92
    assert am.opportunity_score({"detectorSignal": .9415, "commercialValue": .90, "technicalFeasibility": .92,
                                 "operationalBenefit": .86, "strategicAlignment": .98},
                                TELCO_SCORING["weights"], 60)["score"] == 92


def test_rounding_half_up_and_missing_inputs_renormalise_or_refuse():
    assert am.round_half_up(92.5) == 93 and am.round_half_up(79.5) == 80 and am.round_half_up(0.0005 * 1000) == 1
    partial = am.opportunity_score({"detectorSignal": .9, "commercialValue": None, "technicalFeasibility": .8,
                                    "operationalBenefit": None, "strategicAlignment": 1.0},
                                   TELCO_SCORING["weights"], 60)
    assert partial["coverage"] == 65 and partial["score"] == round(100 * (35 * .9 + 20 * .8 + 10 * 1) / 65)
    assert partial["inputs"]["commercialValue"] == {"value": None, "weight": 20, "contribution": None}
    thin = am.opportunity_score({"detectorSignal": .9, "commercialValue": None, "technicalFeasibility": None,
                                 "operationalBenefit": None, "strategicAlignment": 1.0}, TELCO_SCORING["weights"], 60)
    assert thin["score"] is None and thin["coverage"] == 45  # never a low score from missing evidence


def test_percentile_is_midrank():
    assert am.percentile(5, [1, 5, 9]) == 0.5 and am.percentile(10, [1, 5, 9]) == 1.0 and am.percentile(0, []) == 0.0


def test_duplicate_technically_equivalent_consolidates_with_price_condition():
    v1 = prod("V1", replacement="V2", lifecycle="END_OF_SALE", protected_contracts=4209, subscriptions=86214,
              mrr=480000.0)
    v2 = prod("V2")
    r = am.assess(cand(["V1", "V2"], "DUPLICATE", 940), ctx([v1, v2]))
    rec = r["recommendation"]
    assert r["dimensions"]["TECHNICAL"] == {"status": "ASSESSED", "score": 100, "coverage": 100, "missingDomains": [],
                                            "staleDomains": [], "conflicts": [], "reasons": []}
    assert (rec["action"], rec["targetProductId"], rec["sourceProductId"]) == ("CONSOLIDATE", "V2", "V1")
    assert rec["conditions"] == ["PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL"]
    assert rec["text"] == "Move V1 to V2. Preserve contracted price until renewal."
    assert r["similarity"] == 940 and r["impact"]["activeSubscriptions"] == 86214
    assert r["impact"]["summary"] == "£480k / 86k subs"


def test_rule4_duplicate_but_technically_different_is_not_consolidated():
    v1 = prod("V1", replacement="V2", services={"CFS-LEGACY"}, resources={"RES-LEG"}, legacy_billing=True,
              workflows=[{"id": "WF-L", "legacy": True, "compatibility": "PARTIAL"}])
    r = am.assess(cand(["V1", "V2"], "DUPLICATE", 850), ctx([v1, prod("V2")]))
    rec = r["recommendation"]
    assert r["dimensions"]["TECHNICAL"]["score"] < 80
    assert rec["action"] == "MIGRATE" and "STANDARDISE_OR_MIGRATE_BEFORE_CONSOLIDATE" in rec["reasonCodes"]
    clean = prod("V1", replacement="V2", services={"CFS-OTHER"}, resources={"RES-OTHER"}, inventory_active=0,
                 subscriptions=0)
    assert am.assess(cand(["V1", "V2"], "DUPLICATE", 850), ctx([clean, prod("V2")]))["recommendation"]["action"] \
        == "STANDARDISE"


def test_gap_branch_technical_not_assessed_keeps_preliminary_consolidate():
    v1, v2 = prod("V1", replacement="V2"), prod("V2")
    cov = {"V1": ALL_DOMAINS - {"PROVISIONING"}, "V2": set(ALL_DOMAINS)}
    r = am.assess(cand(["V1", "V2"], "DUPLICATE", 940), ctx([v1, v2], coverage=cov))
    assert r["dimensions"]["TECHNICAL"]["status"] == "NOT_ASSESSED" and r["dimensions"]["TECHNICAL"]["score"] is None
    rec = r["recommendation"]
    assert rec["action"] == "CONSOLIDATE" and rec["preliminary"] is True
    assert r["guardrail"] == "NOT_ASSESSED"  # the technical gate applies to CONSOLIDATE
    [b] = [b for b in r["blockers"] if b.category == "EVIDENCE"]
    assert (b.domain, b.consequence, b.facts["domain"]) == ("PROVISIONING", "BLOCKED", "PROVISIONING")
    assert am.candidate_status([{"category": "EVIDENCE", "consequence": "BLOCKED"}], r["guardrail"]) == "EVIDENCE_GAP"


def test_rule3_obsolete_with_footprint_migrates_never_retires():
    old = prod("OLD", lifecycle="RETIRED", status="WITHDRAWN", replacement="NEW", inventory_active=1240)
    new = prod("NEW")
    r = am.assess(cand(["OLD"], "LEGACY", 1000), ctx([old, new]))
    assert r["recommendation"]["action"] in ("MIGRATE", None)  # MIGRATE, or no recommendation if below review
    assert "ACTIVE_FOOTPRINT_MIGRATE_NOT_RETIRE" in r["recommendation"]["reasonCodes"]
    gone = prod("GONE", lifecycle="RETIRED", inventory_active=0, subscriptions=0)
    prelim = am.preliminary_action(cand(["GONE"], "LEGACY", 1000), ctx([gone]),
                                   {"COMMERCIAL": {"status": "ASSESSED"}, "TECHNICAL": {"status": "ASSESSED"}})
    assert prelim["action"] == "RETIRE"
    unknown = prod("UNK", lifecycle="RETIRED", inventory_active=None, subscriptions=None)
    prelim = am.preliminary_action(cand(["UNK"], "LEGACY", 1000), ctx([unknown]),
                                   {"COMMERCIAL": {"status": "ASSESSED"}, "TECHNICAL": {"status": "ASSESSED"}})
    assert prelim["action"] == "INVESTIGATE" and prelim["reasons"] == ["ACTIVE_FOOTPRINT_UNKNOWN"]


def test_rule5_missing_commercial_evidence_investigates():
    a, b = prod("A"), prod("B")
    cov = {"A": ALL_DOMAINS - {"CRM_CATALOG"}, "B": set(ALL_DOMAINS)}
    r = am.assess(cand(["A", "B"], "DUPLICATE", 950), ctx([a, b], coverage=cov))
    assert r["dimensions"]["COMMERCIAL"]["status"] == "NOT_ASSESSED"
    assert r["recommendation"]["action"] == "INVESTIGATE" and r["guardrail"] == "NOT_ASSESSED"


def test_treatments_block_and_partial():
    a, b = prod("A"), prod("B")
    cov = {"A": ALL_DOMAINS - {"CRM_CATALOG"}, "B": set(ALL_DOMAINS)}
    c = ctx([a, b], coverage=cov)
    c.settings["missingEvidenceTreatment"] = "BLOCK"
    assert am.assess(cand(["A", "B"], "DUPLICATE", 950), c)["dimensions"]["COMMERCIAL"]["status"] == "BLOCKED"
    c = ctx([a, b], conflicted={"A": ["Product.productName"]})
    r = am.assess(cand(["A", "B"], "DUPLICATE", 950), c)
    assert r["dimensions"]["COMMERCIAL"]["status"] == "BLOCKED" and r["guardrail"] == "BLOCKED"
    assert any(x.category == "VALIDATION" and x.consequence == "BLOCKED" for x in r["blockers"])
    c.settings["criticalConflictTreatment"] = "REQUIRE_STEWARD_REVIEW"
    r = am.assess(cand(["A", "B"], "DUPLICATE", 950), c)
    assert r["dimensions"]["COMMERCIAL"]["status"] == "PARTIAL"
    assert any(x.category == "VALIDATION" and x.consequence == "WARNING_ONLY" for x in r["blockers"])


def test_below_review_treatments():
    c = ctx([prod("A"), prod("B")])
    weak = cand(["A", "B"], "OVERLAP", 700)
    prelim = {"action": "SIMPLIFY", "target": "B", "source": "A", "reasons": ["OVERLAPPING_OFFERS"],
              "preliminary": False}
    guard, _ = am.guardrail(55, {"COMMERCIAL": {"status": "ASSESSED"}, "TECHNICAL": {"status": "ASSESSED"}},
                            "SIMPLIFY", c)
    assert guard == "BELOW_REVIEW"
    rec = am.recommendation(weak, c, prelim, guard)
    assert rec["action"] is None and rec["text"] is None and "BELOW_REVIEW_THRESHOLD" in rec["reasonCodes"]
    c.settings["belowReviewTreatment"] = "INVESTIGATE"
    assert am.recommendation(weak, c, prelim, guard)["action"] == "INVESTIGATE"
    c.settings["belowReviewTreatment"] = "HIDE_FROM_DEFAULT_VIEW"
    hidden = am.recommendation(weak, c, prelim, guard)
    assert hidden["hidden"] is True and hidden["action"] == "SIMPLIFY"  # hidden, never deleted
    assert hidden["text"] == "Rationalise the overlap: keep B, simplify A."


def test_guardrail_states():
    c = ctx([prod("A")])
    ok = {"COMMERCIAL": {"status": "ASSESSED"}, "TECHNICAL": {"status": "ASSESSED"}}
    assert am.guardrail(85, ok, "SIMPLIFY", c)[0] == "RECOMMENDED"
    assert am.guardrail(79, ok, "SIMPLIFY", c)[0] == "REVIEW_REQUIRED"
    partial = {"COMMERCIAL": {"status": "PARTIAL"}, "TECHNICAL": {"status": "ASSESSED"}}
    assert am.guardrail(95, partial, "SIMPLIFY", c)[0] == "REVIEW_REQUIRED"  # high score but partial evidence
    no_tech = {"COMMERCIAL": {"status": "ASSESSED"}, "TECHNICAL": {"status": "NOT_ASSESSED"}}
    assert am.guardrail(95, no_tech, "SIMPLIFY", c)[0] == "RECOMMENDED"  # gate only for CONSOLIDATE
    assert am.guardrail(95, no_tech, "CONSOLIDATE", c)[0] == "NOT_ASSESSED"
    assert am.guardrail(None, ok, "SIMPLIFY", c)[0] == "NOT_ASSESSED"


def test_priority_bands_overrides_and_independence_from_score():
    old = prod("OLD", lifecycle="SUNSET", sunset=date(2026, 12, 31), legacy_billing=True, subscriptions=1000)
    r = am.assess(cand(["OLD"], "LEGACY", 700), ctx([old, prod("X", subscriptions=500)]))
    pr = r["priority"]
    assert pr["sunsetDays"] == 44 and pr["factors"]["legacyUrgency"]["value"] == 100
    assert {o["kind"] for o in pr["overrides"]} >= {"SUNSET", "DEPENDENCY"}
    assert pr["effective"] == "CRITICAL"  # overrides raise, never lower
    assert pr["factors"]["regulatoryRisk"]["value"] is None  # no evidence: not assessed, not 0
    assert am._band(39, TELCO_PRIORITY["bands"]) == "LOW" and am._band(40, TELCO_PRIORITY["bands"]) == "MEDIUM"
    assert am._band(85, TELCO_PRIORITY["bands"]) == "CRITICAL"


def test_dependency_blockers_and_status_precedence():
    old = prod("OLD", lifecycle="RETIRED", replacement="NEW", inventory_active=50, legacy_billing=True,
               workflows=[{"id": "WF-3G", "legacy": True, "compatibility": "INCOMPATIBLE"}], mrr=9e6)
    new = prod("NEW", billing_systems=set(), legacy_billing=None)
    c = ctx([old, new])
    rec = {"action": "MIGRATE", "sourceProductId": "OLD", "targetProductId": "NEW"}
    drafts = am.blockers(cand(["OLD"], "LEGACY", 1000), c, rec,
                         {d: {"staleDomains": []} for d in am.DIMENSIONS})
    rules = {d.rule: d for d in drafts}
    assert {"PROVISIONING_INCOMPATIBLE", "BILLING_TARGET_MISSING"} <= set(rules)
    assert rules["PROVISIONING_INCOMPATIBLE"].category == "DEPENDENCY"
    inc = rules["PROVISIONING_INCOMPATIBLE"]
    assert inc.due_date == AS_OF + timedelta(days=am.SLA_DAYS[inc.severity])
    assert inc.owner_role == "enterprise_architect" and rules["BILLING_TARGET_MISSING"].domain == "BILLING"
    assert am.candidate_status([{"category": "DEPENDENCY", "consequence": "BLOCKED"},
                                {"category": "EVIDENCE", "consequence": "BLOCKED"}], "RECOMMENDED") == "BLOCKED"
    assert am.candidate_status([{"category": "EVIDENCE", "consequence": "WARNING_ONLY"}], "RECOMMENDED") == "REVIEW"
    assert am.candidate_status([], "RECOMMENDED", "DRAFT") == "DECISION"
    assert am.candidate_status([], "RECOMMENDED", "IN_APPROVAL") == "APPROVAL"
    assert am.candidate_status([{"category": "EVIDENCE", "consequence": "BLOCKED"}], "X", "IN_APPROVAL") == \
        "EVIDENCE_GAP"


def test_customer_exposure_raises_blocker_severity_not_priority_copy():
    big = prod("BIG", lifecycle="RETIRED", replacement="NEW", inventory_active=10, subscriptions=90000,
               workflows=[{"id": "WF-X", "legacy": True, "compatibility": "INCOMPATIBLE"}])
    drafts = am.blockers(cand(["BIG"], "LEGACY", 1000), ctx([big, prod("NEW")]),
                         {"action": "MIGRATE", "sourceProductId": "BIG", "targetProductId": "NEW"},
                         {d: {"staleDomains": []} for d in am.DIMENSIONS})
    assert next(d for d in drafts if d.rule == "PROVISIONING_INCOMPATIBLE").severity == "CRITICAL"


def test_assessment_is_reproducible():
    v1 = prod("V1", replacement="V2", protected_contracts=5)
    a = am.assess(cand(["V1", "V2"], "DUPLICATE", 910), ctx([v1, prod("V2")]))
    b = am.assess(cand(["V1", "V2"], "DUPLICATE", 910), ctx([prod("V2"), v1]))
    strip = lambda r: {k: v for k, v in r.items() if k != "blockers"}  # noqa: E731
    assert strip(a) == strip(b)


@pytest.mark.parametrize("amount,cur,expected", [(480000, "GBP", "£480k"), (1602500, "GBP", "£1.6m"),
                                                 (999, "EUR", "€999"), (2000000, "XYZ", "2m XYZ")])
def test_money_format(amount, cur, expected):
    assert am._money(amount, cur) == expected
