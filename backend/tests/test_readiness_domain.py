"""Step 7: evidence readiness engine (pure). Coverage measures, statuses and thresholds, stale critical evidence,
texts, KPIs, assessments, dispositions (CLAUDE.md rules 1, 2 and 6) and reproducibility."""

import copy
import json

from app.domain.artifact_content import validate_content
from app.domain.readiness import Evidence, compute, key_value
from app.seed.telco import TELCO_CANONICAL, TELCO_READINESS

ALL = [d["domain"] for d in TELCO_READINESS["domains"]]


def k(*parts):
    return json.dumps(list(parts))


def products(ev, n, *, prefix="PRD-", skip_fields=()):
    for i in range(1, n + 1):
        values = {"productId": f"{prefix}{i}", "productName": f"P{i}", "productFamily": "Postpaid",
                  "status": "ACTIVE"}
        for f in skip_fields:
            values.pop(f, None)
        ev.records[("Product", k(f"{prefix}{i}"))] = values


def ref(ev, entity, pid, extra=""):
    ev.records[(entity, k(pid, extra or "x"))] = {"productId": pid}


def by(result, key, field="domain"):
    group = {"domain": "domains", "key": "assessments", "disposition": "dispositions"}[field]
    return next(x for x in result[group] if x[field] == key)


def test_seed_policy_is_valid():
    assert validate_content("READINESS_POLICY", TELCO_READINESS, canonical=TELCO_CANONICAL) == []


def test_policy_checks_reject_bad_content():
    bad = copy.deepcopy(TELCO_READINESS)
    bad["domains"][0]["domain"] = "NOPE"
    bad["domains"][1]["measures"] = [{"type": "RECORD_REFERENCING", "entity": "CpqRule"}]  # field missing
    bad["dispositions"]["RETIRE"] = ["ALSO_NOPE"]
    msgs = [e.message for e in validate_content("READINESS_POLICY", bad, canonical=TELCO_CANONICAL)]
    assert any("Unknown evidence domain NOPE" in m for m in msgs)
    assert any("RECORD_REFERENCING needs field" in m for m in msgs)
    assert any("ALSO_NOPE" in m for m in msgs)
    schema_bad = copy.deepcopy(TELCO_READINESS)
    schema_bad["domains"][2]["measures"] = [{"type": "RECORD_REFERENCING", "entity": "PriceBookEntry",
                                             "field": "nothing"}]
    msgs = [e.message for e in validate_content("READINESS_POLICY", schema_bad, canonical=TELCO_CANONICAL)]
    assert msgs == ["PriceBookEntry.nothing is not in the schema"]
    missing_disp = copy.deepcopy(TELCO_READINESS)
    del missing_disp["dispositions"]["INVESTIGATE"]
    assert validate_content("READINESS_POLICY", missing_disp, canonical=TELCO_CANONICAL)


def test_no_evidence_is_missing_never_zero_scored():
    r = compute(TELCO_READINESS, Evidence(), ALL)
    assert {d["status"] for d in r["domains"]} == {"NOT_ASSESSED"}
    assert {d["status_label"] for d in r["domains"]} == {"Missing"}
    assert r["kpis"] == {"overall_completeness": 0, "domains_available": 0, "domains_selected": 9,
                         "assessment_limitations": 7, "approval_blockers": 7, "products": 0}
    assert by(r, "COMMERCIAL_DUPLICATES", "key")["status"] == "BLOCKED"  # CRM missing -> assessment blocked
    assert by(r, "TECHNICAL_EQUIVALENCE", "key")["status"] == "NOT_ASSESSED"
    assert [d["disposition"] for d in r["dispositions"] if d["ready"]] == ["INVESTIGATE"]


def test_status_thresholds_and_texts():
    ev = Evidence()
    products(ev, 8)
    for i in range(1, 8):  # 7 of 8 CPQ -> 87% AVAILABLE
        ref(ev, "CpqRule", f"PRD-{i}")
    for i in range(1, 5):  # 4 of 8 price -> 50% PARTIAL
        ref(ev, "PriceBookEntry", f"PRD-{i}")
    r = compute(TELCO_READINESS, ev, ALL)
    crm, cpq, price, life = (by(r, d) for d in ("CRM_CATALOG", "CPQ", "PRICE_BOOK", "LIFECYCLE"))
    assert (crm["status"], crm["coverage"]) == ("AVAILABLE", 100)
    assert (cpq["status"], cpq["coverage"], cpq["covered"], cpq["total"]) == ("AVAILABLE", 87, 7, 8)
    assert (price["status"], price["status_label"], price["coverage"]) == ("PARTIAL", "Partial", 50)
    assert price["consequence"] == "Pricing overlap and value comparison unavailable; economics partial for 4 of " \
                                   "8 products."
    assert price["action"] == "Upload price book metadata for the 4 products not covered."
    assert (life["status"], life["status_label"], life["coverage"]) == ("NOT_ASSESSED", "Missing", 0)
    assert life["consequence"].startswith("End-of-sale, grandfathering and sunset cannot be verified")
    assert crm["consequence"] == "None: 8 of 8 products covered." and crm["action"] == "No action needed."
    assert crm["importance_label"] == "Critical" and by(r, "INVENTORY")["importance_label"] == \
        "Critical for Retire/Migrate"


def test_threshold_boundary_80_percent():
    ev = Evidence()
    products(ev, 5)
    for i in range(1, 5):
        ref(ev, "CpqRule", f"PRD-{i}")
    assert by(compute(TELCO_READINESS, ev, ALL), "CPQ")["status"] == "AVAILABLE"  # 4/5 = 80
    ev.records = {key: v for key, v in ev.records.items() if key != ("CpqRule", k("PRD-4", "x"))}
    assert by(compute(TELCO_READINESS, ev, ALL), "CPQ")["status"] == "PARTIAL"  # 3/5 = 60


def test_crm_needs_identity_fields_and_conflicted_fields_do_not_count():
    ev = Evidence()
    products(ev, 4)
    products(ev, 1, prefix="NOFAM-", skip_fields=("productFamily",))
    ev.conflicted.add(("Product", k("PRD-1"), "productName"))
    crm = by(compute(TELCO_READINESS, ev, ALL), "CRM_CATALOG")
    assert (crm["covered"], crm["total"], crm["coverage"], crm["status"]) == (3, 5, 60, "PARTIAL")
    assert crm["conflicted_facts"] == 1


def test_technical_provisioning_inventory_walk_the_graph():
    ev = Evidence()
    products(ev, 2)
    ev.edges += [("DECOMPOSES_TO", "Product", k("PRD-1"), "ServiceSpec", k("CFS-1")),
                 ("DECOMPOSES_TO", "Product", k("PRD-2"), "ServiceSpec", k("CFS-2")),
                 ("PROVISIONED_BY", "Product", k("PRD-1"), "ProvisioningWorkflow", k("WF-1")),
                 ("PROVISIONED_BY", "ServiceSpec", k("CFS-2"), "ProvisioningWorkflow", k("WF-2"))]
    ev.records[("InventoryAggregate", k("AGG-1"))] = {"aggregateKey": "AGG-1", "serviceSpecId": "CFS-1"}
    r = compute(TELCO_READINESS, ev, ALL)
    assert by(r, "TECHNICAL_CATALOG")["coverage"] == 100
    assert by(r, "PROVISIONING")["coverage"] == 100  # PRD-2 through its service
    assert (by(r, "INVENTORY")["covered"], by(r, "INVENTORY")["status"]) == (1, "PARTIAL")


def test_order_billing_needs_aggregate_and_billing_link():
    ev = Evidence()
    products(ev, 2)
    ref(ev, "OrderAssuranceAggregate", "PRD-1", "2026-10-31")
    ref(ev, "OrderAssuranceAggregate", "PRD-2", "2026-10-31")
    ev.edges.append(("BILLED_BY", "Product", k("PRD-1"), "BillingSystem", k("BILL-A")))
    oba = by(compute(TELCO_READINESS, ev, ALL), "ORDER_BILLING_ASSURANCE")
    assert (oba["covered"], oba["status"], oba["importance"]) == (1, "PARTIAL", "HIGH")


def test_stale_critical_domain_is_blocked_other_importance_is_only_flagged():
    ev = Evidence()
    products(ev, 2)
    ref(ev, "CpqRule", "PRD-1")
    ref(ev, "CpqRule", "PRD-2")
    ev.stale_domains = {"CRM_CATALOG", "CPQ", "INVENTORY"}
    r = compute(TELCO_READINESS, ev, ALL)
    crm, cpq, inv = by(r, "CRM_CATALOG"), by(r, "CPQ"), by(r, "INVENTORY")
    assert (crm["status"], crm["stale"], crm["coverage"]) == ("BLOCKED", True, 100)
    assert crm["consequence"].startswith("Evidence is older than the freshness limit")
    assert (cpq["status"], cpq["stale"]) == ("AVAILABLE", True)  # HIGH: flag only
    assert inv["status"] == "NOT_ASSESSED"  # stale but no data: still missing
    assert by(r, "COMMERCIAL_DUPLICATES", "key")["status"] == "BLOCKED"
    assert by(r, "RETAIN", "disposition")["missing"] == [{"domain": "CRM_CATALOG", "status": "BLOCKED"}]
    assert r["kpis"]["overall_completeness"] == round((3 * 100 + 2 * 100) / 22)  # stale does not reduce it


def test_weighted_completeness_assessments_and_dispositions():
    ev = Evidence()
    products(ev, 4)
    for i in (1, 2):
        ref(ev, "CpqRule", f"PRD-{i}")
    selected = ["CRM_CATALOG", "CPQ", "TECHNICAL_CATALOG"]
    r = compute(TELCO_READINESS, ev, selected)
    # CRM 100 (x3) + CPQ 50 (x2) + technical 0 (x3) over weight 8
    assert r["kpis"]["overall_completeness"] == round((300 + 100) / 8) == 50
    assert (r["kpis"]["domains_selected"], r["kpis"]["domains_available"], r["kpis"]["products"]) == (3, 1, 4)
    assert by(r, "PRICE_BOOK")["selected"] is False
    dup = by(r, "COMMERCIAL_DUPLICATES", "key")
    assert dup["status"] == "LIMITED" and dup["limited_by"] == ["CPQ", "PRICE_BOOK"]
    assert by(r, "CONFIGURATION_COMPLEXITY", "key")["status"] == "LIMITED"
    assert by(r, "MIGRATION_READINESS", "key")["status"] == "BLOCKED"
    ready = {d["disposition"]: d["ready"] for d in r["dispositions"]}
    assert ready == {"RETAIN": True, "SIMPLIFY": False, "CONSOLIDATE": False, "STANDARDISE": False,
                     "MIGRATE": False, "RETIRE": False, "INVESTIGATE": True}
    cons = by(r, "CONSOLIDATE", "disposition")
    assert cons["missing"] == [{"domain": "TECHNICAL_CATALOG", "status": "NOT_ASSESSED"},
                               {"domain": "PROVISIONING", "status": "NOT_ASSESSED"}]
    # gating domains: CRM, CPQ, LIFECYCLE, TECHNICAL, PROVISIONING, INVENTORY, CUSTOMER_REVENUE; only CRM available
    assert r["kpis"]["approval_blockers"] == 6


def test_full_evidence_makes_every_disposition_ready():
    ev = Evidence()
    products(ev, 1)
    for e in ("CpqRule", "PriceBookEntry", "ProductLifecycle", "OrderAssuranceAggregate",
              "CustomerRevenueAggregate"):
        ref(ev, e, "PRD-1")
    ev.edges += [("DECOMPOSES_TO", "Product", k("PRD-1"), "ServiceSpec", k("CFS-1")),
                 ("PROVISIONED_BY", "ServiceSpec", k("CFS-1"), "ProvisioningWorkflow", k("WF-1")),
                 ("BILLED_BY", "Product", k("PRD-1"), "BillingSystem", k("BILL-A"))]
    ev.records[("InventoryAggregate", k("AGG-1"))] = {"serviceSpecId": "CFS-1"}
    r = compute(TELCO_READINESS, ev, ALL)
    assert all(d["status"] == "AVAILABLE" for d in r["domains"])
    assert all(d["ready"] for d in r["dispositions"])
    assert all(a["status"] == "AVAILABLE" for a in r["assessments"])
    assert r["kpis"]["overall_completeness"] == 100 and r["kpis"]["approval_blockers"] == 0


def test_result_hash_is_reproducible():
    def build():
        ev = Evidence()
        products(ev, 3)
        ref(ev, "CpqRule", "PRD-2")
        return ev
    a, b = compute(TELCO_READINESS, build(), ALL), compute(TELCO_READINESS, build(), ALL)
    assert a["result_hash"] == b["result_hash"] and a["result_hash"].startswith("sha256:")
    ev = build()
    ref(ev, "CpqRule", "PRD-3")
    assert compute(TELCO_READINESS, ev, ALL)["result_hash"] != a["result_hash"]


def test_key_value():
    assert key_value('["PRD-1"]') == "PRD-1" and key_value('["A", "B"]') == ["A", "B"]
