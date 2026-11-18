"""Step 10: detectors and candidate normalization (pure). Factors, NOT_ASSESSED handling, thresholds, P95 baseline,
grouping, primary type precedence, batch-count reconciliation, reproducibility."""

from datetime import date

import pytest

from app.domain import candidates as cand
from app.domain import detectors as det
from app.seed.configuration import TELCO_DETECTORS

TODAY = date(2026, 11, 17)


def p(pid, **kw):
    base = dict(name=f"Product {pid}", family="POSTPAID", segment="Residential", market="UK Consumer",
                channel="Online")
    base.update(kw)
    return det.ProductFeatures(product_id=pid, **base)


V1 = dict(name="Mobile Data 15GB V1", cpq_options={"OPT-DATA-15GB"}, prices={"GBP": 15.0}, lifecycle="END_OF_SALE",
          replacement="PRD-V2", services={"CFS-LEGACY"}, service_types={"mobile data"}, resources={"RES-LEG"})
V2 = dict(name="Mobile Data 15GB V2", cpq_options={"OPT-DATA-15GB"}, prices={"GBP": 15.0}, lifecycle="ACTIVE",
          services={"CFS-5G"}, service_types={"mobile data"}, resources={"RES-5G"})


def test_tokens_ignore_versions():
    assert det.tokens("Mobile Data 15GB V1") == det.tokens("Mobile Data 15GB v2") == {"mobile", "data", "15gb"}


def test_duplicate_probable_but_technically_different():
    findings, summary = det.run_detectors([p("PRD-V1", **V1), p("PRD-V2", **V2)], TELCO_DETECTORS, TODAY)
    dup = next(f for f in findings if f.detector_type == "DUPLICATE")
    assert (dup.subjects, dup.signal, dup.band, dup.coverage) == (["PRD-V1", "PRD-V2"], 850, "PROBABLE", 100)
    assert dup.factors["technical"] == {"value": 0, "weight": 15}
    assert dup.factors["lifecycle"]["value"] == 1000  # V1 is replaced by V2
    assert summary["duplicate"] == 1 and not any(f.detector_type == "OVERLAP" for f in findings)


def test_strong_duplicate_and_missing_factors_are_not_zero():
    a = p("A", **{**V2, "name": "Fibre 500"})
    b = p("B", **{**V2, "name": "Fibre 500"})
    f = det.run_detectors([a, b], TELCO_DETECTORS, TODAY)[0][0]
    assert f.band == "STRONG" and f.signal == 1000
    # remove CPQ and price on one side: those factors are NOT_ASSESSED, weights re-normalised, still strong
    b2 = p("B", **{**V2, "name": "Fibre 500", "cpq_options": set(), "prices": {}})
    f = det.run_detectors([a, b2], TELCO_DETECTORS, TODAY)[0][0]
    assert f.factors["cpq"]["value"] is None and f.factors["pricingStructure"]["value"] is None
    assert f.signal == 1000 and f.coverage == 70
    # too little evidence (< 60% of the weight) -> no finding rather than a low score
    thin_a = p("A", name="Fibre 500", segment=None, market=None, channel=None)
    thin_b = p("B", name="Fibre 500", segment=None, market=None, channel=None)
    assert det.run_detectors([thin_a, thin_b], TELCO_DETECTORS, TODAY)[0] == []


def test_overlap_requires_capability_and_no_duplicate():
    a = p("A", name="Mobile Data 30GB", services={"CFS-5G"}, service_types={"mobile data"})
    b = p("B", name="Mobile Data 15GB", services={"CFS-5G"}, service_types={"mobile data"})
    findings, _ = det.run_detectors([a, b], TELCO_DETECTORS, TODAY)
    ov = [f for f in findings if f.detector_type == "OVERLAP"]
    # substitution has no lifecycle evidence: NOT_ASSESSED, so (.40 + .25 + .15 + .075) / .95
    assert len(ov) == 1 and (ov[0].signal, ov[0].coverage) == (921, 95)
    other_family = p("C", family="PREPAID", name="Mobile Data 30GB", services={"CFS-5G"})
    assert all("C" not in f.subjects for f in det.run_detectors([a, other_family], TELCO_DETECTORS, TODAY)[0])


def test_legacy_conditions_and_installed_base_note():
    old = p("OLD", lifecycle="RETIRED", workflows=[{"legacy": True}], inventory_active=1240, legacy_billing=True)
    fresh = p("NEW", lifecycle="ACTIVE", workflows=[{"legacy": False}], legacy_billing=False)
    eos_soon = p("EOS", end_of_sale=date(2026, 1, 1))
    findings = det.legacy_findings([old, fresh, eos_soon], TELCO_DETECTORS, TODAY)
    assert [f.subjects[0] for f in findings] == ["EOS", "OLD"]
    o = findings[1]
    assert o.signal == 900 and o.factors["replacementWithLegacyArchitecture"]["value"] is None
    assert o.notes == ["Installed base 1240 active services: migrate, never retire silently"]


def test_p95_baseline_and_operational_burden():
    small = [p(f"P{i}", orders=1000, fallout=i * 10, manual_touch=5, incidents=2, provisioning_exceptions=1,
               handling_minutes=5.0) for i in range(1, 6)]
    assert det.single_findings(small, TELCO_DETECTORS) == []  # 5 < MIN_BASELINE: not assessed
    big = [p(f"P{i:02d}", orders=1000, fallout=10, manual_touch=5, incidents=2, provisioning_exceptions=1,
             handling_minutes=5.0) for i in range(1, 12)]
    big[0] = p("P01", orders=1000, fallout=90, manual_touch=60, incidents=30, provisioning_exceptions=20,
               handling_minutes=40.0)
    found = det.single_findings(big, TELCO_DETECTORS)
    burden = [f for f in found if f.detector_type == "OPERATIONAL_BURDEN"]
    assert [f.subjects for f in burden] == [["P01"]] and burden[0].signal == 1000
    assert det.p95([1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12, 13, 14, 15, 16, 17, 18, 19, 100]) == 19


def test_complexity_hard_limit():
    huge = p("HUGE", cpq_rule_count=500)
    f = det.single_findings([huge], TELCO_DETECTORS)
    assert f[0].detector_type == "COMPLEXITY" and "Hard limit exceeded: rules" in f[0].notes


def test_normalisation_groups_counts_and_precedence():
    findings, _ = det.run_detectors([p("PRD-V1", **V1, workflows=[{"legacy": True}]), p("PRD-V2", **V2)],
                                    TELCO_DETECTORS, TODAY)
    extra = det.Finding(det.finding_key("COMPLEXITY", ["PRD-V1"]), "COMPLEXITY", "1.0", ["PRD-V1"], 800, 750, "MATCH",
                        {}, 100, ["EV-x"])
    ghost = det.Finding("ghost", "LEGACY", "1.0", ["PRD-GONE"], 700, 700, "MATCH", {}, 100, [])
    outside = det.Finding("out", "LEGACY", "1.0", ["PRD-OUT"], 700, 700, "MATCH", {}, 100, [])
    all_f = findings + [extra, ghost, outside]
    n = cand.normalise(all_f, run_id="r", snapshot_id="s", pack=TELCO_DETECTORS, pack_ref="P@1.0",
                       snapshot_products={"PRD-V1", "PRD-V2", "PRD-OUT"}, in_scope={"PRD-V1", "PRD-V2"})
    assert n.counts == {"rawFindings": 5, "merged": 1, "invalid": 1, "outOfScope": 1, "superseded": 0,
                        "candidates": 2}
    single = next(c for c in n.candidates if c.subjects == ["PRD-V1"])
    assert single.primary_type == "LEGACY" and single.supporting_types == ["COMPLEXITY"]
    assert single.max_signal == max(f.signal for f in all_f if f.subjects == ["PRD-V1"])
    assert n.finding_status["ghost"] == "INVALID" and n.finding_status["out"] == "OUT_OF_SCOPE"
    key = cand.group_key("r", "s", "PRODUCT_SET", ["PRD-V2", "PRD-V1"], "P@1.0")
    assert key == cand.group_key("r", "s", "PRODUCT_SET", ["PRD-V1", "PRD-V2"], "P@1.0")
    assert key != cand.group_key("r", "s2", "PRODUCT_SET", ["PRD-V1", "PRD-V2"], "P@1.0")


def test_reproducible():
    run1 = det.run_detectors([p("PRD-V1", **V1), p("PRD-V2", **V2)], TELCO_DETECTORS, TODAY)[0]
    run2 = det.run_detectors([p("PRD-V2", **V2), p("PRD-V1", **V1)], TELCO_DETECTORS, TODAY)[0]
    assert [f.as_dict() for f in run1] == [f.as_dict() for f in run2]


@pytest.mark.parametrize("a,b,expected", [({"x"}, {"x"}, 1.0), ({"x"}, {"y"}, 0.0), (set(), {"y"}, None)])
def test_jaccard(a, b, expected):
    assert det.jaccard(a, b) == expected


def test_zero_family_baseline_keeps_above_baseline_outliers():
    """Review fix: a family P95 of 0 (most products have no fallout) must not zero the one product that has some."""
    quiet = [p(f"Q{i:02d}", orders=1000, fallout=0, manual_touch=0, incidents=0, provisioning_exceptions=0,
               handling_minutes=0.0) for i in range(1, 12)]
    quiet[0] = p("Q01", orders=1000, fallout=120, manual_touch=40, incidents=25, provisioning_exceptions=15,
                 handling_minutes=30.0)
    raw = {q.product_id: {"fallout": (q.fallout or 0) / 1000} for q in quiet}
    norm, notes = det._normalised(quiet, raw, ("fallout",))
    assert norm["Q01"]["fallout"] == 1.0 and norm["Q02"]["fallout"] == 0.0
    burden = [f for f in det.single_findings(quiet, TELCO_DETECTORS) if f.detector_type == "OPERATIONAL_BURDEN"]
    assert [f.subjects for f in burden] == [["Q01"]]
