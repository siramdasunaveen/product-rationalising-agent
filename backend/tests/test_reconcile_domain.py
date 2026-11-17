"""Pure reconciliation engine: agreement, precedence, freshness, materiality, decisions, graph checks, hashes."""

from datetime import date

from app.domain import reconcile as rc
from app.seed.telco import TELCO_CANONICAL

TODAY = date(2026, 11, 17)
POLICY = rc.Policy({
    "canonicalSchema": "TELCO-CANONICAL", "defaultFreshnessDays": 90,
    "freshnessDays": {"CRM_CATALOG": 90, "CPQ": 90},
    "precedence": [{"field": "Product.*", "order": ["CRM_CATALOG:SALESFORCE", "CRM_CATALOG"]},
                   {"field": "Offer.*", "order": ["CRM_CATALOG", "CPQ"]}],
    "materialFields": ["Product.productName", "Product.status"],
})


def src(sid, domain="CRM_CATALOG", system="EXCEL", as_of=date(2026, 11, 10), records=(), edges=()):
    return rc.SourceData(rc.SourceInfo(sid, f"Source {sid}", domain, system, as_of, f"ex-{sid}"),
                         [rc.SourceRecord(e, k, v, {f: i for i, f in enumerate(v)}) for e, k, v in records],
                         [rc.SourceEdge(*x) for x in edges])


def prod(key, **values):
    return ("Product", f'["{key}"]', {"productId": key, **values})


def run(*sources, decisions=None):
    return rc.reconcile(list(sources), POLICY, TELCO_CANONICAL, TODAY, decisions)


def test_agreement_and_single_source():
    r = run(src("A", records=[prod("P1", productName="Fibre", market="UK")]),
            src("B", records=[prod("P1", productName="Fibre")]))
    rec = r.records[("Product", '["P1"]')]
    assert rec.values == {"productId": "P1", "productName": "Fibre", "market": "UK"}
    assert rec.provenance["productName"]["rule"] == "AGREED" and rec.provenance["productName"]["sources"] == ["A", "B"]
    assert rec.provenance["market"]["rule"] == "SINGLE_SOURCE"
    assert rec.provenance["productName"]["lineage"] == {"A": 1, "B": 1}
    assert r.conflicts == [] and rec.sources == ["A", "B"]


def test_non_material_conflict_settled_by_precedence_and_shown():
    r = run(src("CRM", records=[("Offer", '["O1"]', {"offerId": "O1", "offerName": "Family deal"})]),
            src("CPQ", domain="CPQ", records=[("Offer", '["O1"]', {"offerId": "O1", "offerName": "Family bundle"})]))
    [c] = r.conflicts
    assert (c.status, c.material, c.recommended_source_id) == ("AUTO_RESOLVED", False, "CRM")
    assert c.reason == "Precedence: CRM_CATALOG owns Offer.*"
    rec = r.records[("Offer", '["O1"]')]
    assert rec.values["offerName"] == "Family deal" and rec.provenance["offerName"]["rule"] == "PRECEDENCE"


def test_system_token_beats_plain_domain():
    r = run(src("DYN", system="DYNAMICS", records=[prod("P1", market="IE")]),
            src("SF", system="SALESFORCE", records=[prod("P1", market="UK")]))
    [c] = r.conflicts
    assert c.recommended_source_id == "SF" and "CRM_CATALOG:SALESFORCE" in c.reason
    assert r.records[("Product", '["P1"]')].values["market"] == "UK"


def test_freshness_breaks_ties_and_stale_sources_are_flagged():
    old = src("OLD", as_of=date(2026, 6, 1), records=[prod("P1", market="UK")])  # older than 90 days
    new = src("NEW", as_of=date(2026, 11, 1), records=[prod("P1", market="IE")])
    r = run(old, new)
    [c] = r.conflicts
    assert c.recommended_source_id == "NEW" and c.reason.startswith("Freshness")
    assert [s["source_id"] for s in r.stale_sources] == ["OLD"]
    assert next(x for x in c.candidates if x["source_id"] == "OLD")["stale"] is True
    newer = run(src("A", as_of=date(2026, 11, 1), records=[prod("P1", market="UK")]),
                src("B", as_of=date(2026, 11, 15), records=[prod("P1", market="IE")]))
    assert newer.conflicts[0].recommended_source_id == "B" and "newer data" in newer.conflicts[0].reason


def test_material_conflict_stays_open_and_leaves_the_field_out():
    r = run(src("A", records=[prod("P1", productName="Fibre 500")]),
            src("B", records=[prod("P1", productName="Fibre 500 Home")]))
    [c] = r.conflicts
    assert (c.status, c.material) == ("OPEN", True)
    rec = r.records[("Product", '["P1"]')]
    assert "productName" not in rec.values
    assert rec.provenance["productName"]["rule"] == "OPEN" and rec.provenance["productName"]["conflicted"]
    assert r.counts()["open_material_conflicts"] == 1
    assert {x["value"] for x in c.candidates} == {"Fibre 500", "Fibre 500 Home"}


def test_steward_decisions_are_applied_and_survive_reruns():
    a = src("A", records=[prod("P1", productName="Fibre 500")])
    b = src("B", records=[prod("P1", productName="Fibre 500 Home")])
    c = run(a, b).conflicts[0]
    key = (c.conflict_key, c.fingerprint)
    sel = run(a, b, decisions={key: rc.StewardDecision("SELECT_SOURCE", "d1", source_id="B")})
    assert sel.records[("Product", '["P1"]')].values["productName"] == "Fibre 500 Home"
    assert sel.conflicts[0].status == "RESOLVED" and sel.conflicts[0].decision_id == "d1"
    man = run(a, b, decisions={key: rc.StewardDecision("MANUAL", "d2", value="Fibre 500 (Home)", rationale="Brand")})
    assert man.records[("Product", '["P1"]')].values["productName"] == "Fibre 500 (Home)"
    defer = run(a, b, decisions={key: rc.StewardDecision("DEFER", "d3", rationale="Ask product team")})
    rec = defer.records[("Product", '["P1"]')]
    assert "productName" not in rec.values and rec.provenance["productName"]["conflicted"]
    assert defer.conflicts[0].status == "DEFERRED" and defer.counts()["open_material_conflicts"] == 0
    # a source changes its value -> different fingerprint -> the old decision no longer applies
    b2 = src("B", records=[prod("P1", productName="Fibre 500 Plus")])
    again = run(a, b2, decisions={key: rc.StewardDecision("SELECT_SOURCE", "d1", source_id="B")})
    assert again.conflicts[0].status == "OPEN"


def test_relationships_union_and_dangling_links():
    a = src("A", records=[prod("P1", productName="Fibre"), prod("P2", productName="Copper", parentProductId="P9")],
            edges=[("INCLUDES", "Offer", '["O1"]', "Product", '["P1"]')])
    b = src("B", domain="CPQ", records=[("Offer", '["O1"]', {"offerId": "O1"})],
            edges=[("INCLUDES", "Offer", '["O1"]', "Product", '["P1"]'),
                   ("DECOMPOSES_TO", "Product", '["P1"]', "ServiceSpec", '["S1"]')])
    r = run(a, b)
    assert [(e.name, e.sources) for e in r.edges] == [("DECOMPOSES_TO", ["B"]), ("INCLUDES", ["A", "B"])]
    kinds = {(d["kind"], d["entity"], d["key"]) for d in r.dangling}
    assert ("relationship", "ServiceSpec", '["S1"]') in kinds  # no source supplied service S1
    assert ("reference", "Product", '["P9"]') in kinds  # parent product P9 missing


def test_possible_duplicates_are_suggestions_only():
    r = run(src("A", records=[prod("P1", productName="Fibre 500 Home"), prod("P2", productName="fibre-500  home")]))
    assert r.possible_duplicates == [{"entity": "Product", "normalised_name": "fibre 500 home", "keys": ['["P1"]',
                                                                                                    '["P2"]']}]
    assert len(r.records) == 2  # never merged


def test_results_and_snapshot_hashes_are_deterministic():
    a = src("A", records=[prod("P1", productName="Fibre", market="UK"), prod("P2", productName="Copper")])
    b = src("B", records=[prod("P1", productName="Fibre", market="IE")])
    r1, r2 = run(a, b), run(b, a)
    assert rc.result_hash(r1) == rc.result_hash(r2)
    f1, e1 = rc.snapshot_facts(r1)
    f2, e2 = rc.snapshot_facts(r2)
    assert rc.snapshot_content_hash(f1, e1) == rc.snapshot_content_hash(f2, e2)
    ids = {(f["entity"], f["record_key"], f["field"]): f["evidence_id"] for f in f1}
    assert ids[("Product", '["P1"]', "productName")] == rc.evidence_id("Product", '["P1"]', "productName", "Fibre")
    assert all(i.startswith("EV-") and len(i) == 15 for i in ids.values())
    changed = run(src("A", records=[prod("P1", productName="Fibre X", market="UK"), prod("P2", productName="Copper")]))
    f3, e3 = rc.snapshot_facts(changed)
    ids3 = {(f["entity"], f["record_key"], f["field"]): f["evidence_id"] for f in f3}
    assert ids3[("Product", '["P2"]', "productName")] == ids[("Product", '["P2"]', "productName")]  # unchanged fact
    assert ids3[("Product", '["P1"]', "productName")] != ids[("Product", '["P1"]', "productName")]
