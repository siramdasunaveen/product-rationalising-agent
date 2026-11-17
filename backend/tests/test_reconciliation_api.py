"""Step 6 end to end: two sources for the same product reconcile with visible lineage and a conflict queue; the
snapshot approval is audited; decisions survive re-runs; snapshots are immutable; the Telco graph walks a product."""

import uuid
from datetime import date, timedelta

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import AuditEvent, FieldLineage, OutboxEvent
from app.domain.excel_checks import FileLimits
from app.services import evidence as ev_service
from app.services import mapping as map_service
from app.services import reconciliation as service
from app.services.artifact_seed import seed_artifacts
from app.services.reference import seed_reference_data
from tests import excel_files as f

API = "/api/rationalisation"
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
LIMITS = FileLimits()
CRM = ["Product ID", "Product Name", "Product Family", "Status", "Offer ID"]
OBA = ["Product ID", "Period Start", "Period End", "Orders", "Fallout Orders", "Billing System ID",
       "Billing System Name", "Legacy Billing Code"]
CRA = ["Product ID", "Period Start", "Period End", "Active Subscriptions", "Protected-Price Contracts", "MRR",
       "Currency"]


@pytest.fixture
def env(client, login, session_factory, tenant, platform):
    with session_factory() as s:
        seed_reference_data(s, tenant)
        s.commit()
        seed_artifacts(s, platform.audit, tenant)
    pm = login("portfolio_manager", tenant)
    me = client.get(f"{API}/me", headers=pm).json()
    pid = {p["code"]: p["id"] for p in client.get(f"{API}/metadata/portfolios", headers=pm).json()}
    mid = {m["code"]: m["id"] for m in client.get(f"{API}/metadata/markets", headers=pm).json()}
    fid = {x["code"]: x["id"] for x in client.get(f"{API}/metadata/product-families", headers=pm).json()}
    run = client.post(f"{API}/runs", headers=pm, json={
        "name": f"Reconcile {uuid.uuid4().hex[:6]}", "portfolio_id": pid["CONSUMER_MOBILE"],
        "market_ids": [mid["UK_CONSUMER"]], "product_family_ids": [fid["POSTPAID"]], "period_start": "2025-11-16",
        "period_end": "2026-11-16", "objectives": ["BSS_SUNSET"], "owner_id": me["user_id"]}).json()
    return {"pm": pm, "steward": login("data_steward", tenant), "steward2": login("data_steward", tenant),
            "admin": login("agent_admin", tenant), "viewer": login("viewer", tenant), "run": run, "tenant": tenant,
            "client": client, "sf": session_factory, "platform": platform}


def R(env):  # noqa: N802 - test helper
    return f"{API}/runs/{env['run']['id']}"


def add_source(env, domain, name, system):
    r = env["client"].post(f"{R(env)}/evidence/{domain}/sources", headers=env["steward"],
                           json={"name": name, "system": system})
    assert r.status_code == 201, r.text
    return r.json()["id"]


def default_source(env, domain):
    """The domain's first Excel source (created by the first upload without a source_id)."""
    ov = env["client"].get(f"{R(env)}/evidence", headers=env["steward"]).json()
    return next(d for d in ov["domains"] if d["key"] == domain)["sources"][0]["id"]


def load(env, domain, headers, rows, *, source_id=None, as_of=None, name="file.xlsx"):
    """Upload -> scan/read -> confirm; returns the upload id."""
    c = env["client"]
    params = {"source_id": source_id} if source_id else {}
    r = c.post(f"{R(env)}/evidence/{domain}/uploads", headers=env["steward"], params=params,
               files={"file": (name, f.xlsx({"Data": [headers, *rows]}), XLSX)})
    assert r.status_code in (200, 202), r.text
    uid = r.json()["id"]
    with env["sf"]() as s:
        ev_service.process_upload(s, env["platform"], LIMITS, uuid.UUID(uid))
    body = {"sheet": "Data", "header_row": 1} | ({"as_of": as_of} if as_of else {})
    assert c.post(f"{R(env)}/evidence/uploads/{uid}/confirm", headers=env["steward"], json=body).status_code == 200
    return uid


def publish_mapping(env, domain, source_id):
    c = env["client"]
    m = c.post(f"{R(env)}/evidence/{domain}/mapping", headers=env["steward"], params={"source_id": source_id},
               json={})
    assert m.status_code == 201, m.text
    m = m.json()
    base = f"{R(env)}/mappings/{m['mapping_id']}/versions/{m['version']}"
    v = c.post(f"{base}/validate", headers=env["steward"]).json()
    with env["sf"]() as s:
        map_service.run_validation(s, env["platform"], LIMITS, uuid.UUID(v["validation"]["id"]))
    m = c.get(base, headers=env["steward"]).json()
    assert m["status"] == "READY_FOR_REVIEW", m["validation"]
    m = c.post(f"{base}/submit", headers=env["steward"],
               json={"lock_version": m["lock_version"], "acknowledge_warnings": True}).json()
    m = c.post(f"{base}/review", headers=env["admin"], json={"decision": "APPROVE",
                                                             "content_hash": m["content_hash"]}).json()
    m = c.post(f"{base}/publish", headers=env["admin"], json={"lock_version": m["lock_version"]}).json()
    assert m["status"] == "PUBLISHED"
    return m["version_id"]


def apply(env, upload_id, version_id):
    r = env["client"].post(f"{R(env)}/evidence/uploads/{upload_id}/apply", headers=env["steward"],
                           json={"mapping_version_id": version_id})
    assert r.status_code in (200, 202), r.text
    with env["sf"]() as s:
        map_service.run_execution(s, env["platform"], LIMITS, uuid.UUID(r.json()["id"]))
    return r.json()["id"]


def reconcile(env):
    r = env["client"].post(f"{R(env)}/reconciliation", headers=env["steward"])
    assert r.status_code == 202, r.text
    with env["sf"]() as s:
        service.run_reconciliation(s, env["platform"], uuid.UUID(r.json()["id"]))
    return env["client"].get(f"{R(env)}/reconciliation", headers=env["steward"]).json()


def conflicts(env, rec_id, **params):
    return env["client"].get(f"{R(env)}/reconciliation/{rec_id}/conflicts", headers=env["steward"],
                             params=params).json()


def two_crm_sources(env):
    """Salesforce and Dynamics exports describe PRD-1 differently (name: material; offer only in one)."""
    today = date.today().isoformat()
    u1 = load(env, "CRM_CATALOG", CRM, [["PRD-1", "Mobile Data 15GB", "Postpaid", "Active", "OFR-1"],
                                        ["PRD-2", "Mobile Data 30GB", "Postpaid", "Active", None]],
              as_of=today, name="salesforce.xlsx")
    sf_src = default_source(env, "CRM_CATALOG")
    dyn_src = add_source(env, "CRM_CATALOG", "Dynamics export", "DYNAMICS")
    u2 = load(env, "CRM_CATALOG", CRM, [["PRD-1", "Mobile Data 15GB V1", "Postpaid", "Live", None]],
              source_id=dyn_src, as_of=today, name="dynamics.xlsx")
    v1 = publish_mapping(env, "CRM_CATALOG", sf_src)
    v2 = publish_mapping(env, "CRM_CATALOG", dyn_src)
    apply(env, u1, v1)
    apply(env, u2, v2)
    return sf_src, dyn_src, (u1, v1), (u2, v2)


# ---- the "done when" scenario ----

def test_two_sources_reconcile_with_lineage_conflict_queue_and_audited_approval(env, db):
    c = env["client"]
    sf_src, dyn_src, _, (u2, v2) = two_crm_sources(env)
    ov = reconcile(env)
    rec = ov["latest_succeeded"]
    assert rec["status"] == "SUCCEEDED" and rec["policy_ref"] == "TELCO-PRECEDENCE@1.0"
    assert rec["canonical_ref"].startswith("TELCO-CANONICAL@") and len(rec["inputs"]) == 2
    assert rec["counts"]["records"] == {"Offer": 1, "Product": 2}
    assert rec["counts"]["open_material_conflicts"] == 1  # productName differs; status agrees (Live = ACTIVE)
    assert ov["approval_blockers"] == ["1 material conflict(s) still need a Data Steward decision"]

    [conflict] = conflicts(env, rec["id"])
    assert (conflict["field"], conflict["status"], conflict["material"]) == ("productName", "OPEN", True)
    by_src = {x["source_id"]: x for x in conflict["candidates"]}
    assert by_src[sf_src]["value"] == "Mobile Data 15GB" and by_src[dyn_src]["value"] == "Mobile Data 15GB V1"
    assert by_src[sf_src]["lineage_id"] and by_src[dyn_src]["system"] == "DYNAMICS"

    blocked = c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "snapshot_blocked"

    no_reason = c.post(f"{R(env)}/reconciliation/conflicts/{conflict['id']}/decision", headers=env["steward"],
                       json={"decision": "DEFER"})
    assert no_reason.status_code == 422
    d = c.post(f"{R(env)}/reconciliation/conflicts/{conflict['id']}/decision", headers=env["steward"],
               json={"decision": "SELECT_SOURCE", "source_id": dyn_src})
    assert d.status_code == 200 and d.json()["status"] == "RESOLVED" and d.json()["decision"]["by"]

    recs = c.get(f"{R(env)}/reconciliation/{rec['id']}/records", headers=env["pm"],
                 params={"entity": "Product"}).json()
    p1 = next(r for r in recs["items"] if r["key"] == '["PRD-1"]')
    assert p1["values"]["productName"] == "Mobile Data 15GB V1"
    assert p1["provenance"]["productName"]["rule"] == "STEWARD_SELECTED"
    assert p1["provenance"]["status"]["rule"] == "AGREED" and sorted(p1["sources"]) == sorted([sf_src, dyn_src])

    snap = c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]})
    assert snap.status_code == 201, snap.text
    snap = snap.json()
    assert (snap["code"], snap["status"], snap["parent_code"]) == ("SNAP-001", "APPROVED", None)
    assert snap["content_hash"].startswith("sha256:") and snap["counts"]["conflicted_facts"] == 0
    twice = c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]})
    assert twice.status_code == 409 and "already approved as SNAP-001" in twice.json()["detail"]["errors"][0]["message"]

    facts = c.get(f"{R(env)}/snapshots/{snap['id']}/facts", headers=env["pm"], params={"entity": "Product"}).json()
    name = next(x for x in facts["items"] if x["key"] == '["PRD-1"]' and x["field"] == "productName")
    assert name["evidence_id"].startswith("EV-") and name["value"] == "Mobile Data 15GB V1"
    lineage_id = name["provenance"]["lineage"][dyn_src]
    with env["sf"]() as s:  # the fact traces back to the cell in the Dynamics file
        lin = s.get(FieldLineage, lineage_id)
        assert (lin.source_field, lin.source_value, lin.source_row, str(lin.upload_id)) == (
            "Product Name", "Mobile Data 15GB V1", 2, u2)

    actions = set(db.scalars(select(AuditEvent.action).where(AuditEvent.tenant_id == env["tenant"])))
    assert {"reconciliation.requested", "reconciliation.completed", "reconciliation.decision_recorded",
            "evidence.snapshot_approved", "evidence.source_added"} <= actions
    events = set(db.scalars(select(OutboxEvent.event_type).where(OutboxEvent.tenant_id == env["tenant"])))
    assert {"DataStewardDecisionRecorded", "EvidenceSnapshotApproved"} <= events


def test_decisions_survive_reconcile_and_new_snapshot_supersedes(env):
    c = env["client"]
    sf_src, dyn_src, (u1, v1), _ = two_crm_sources(env)
    rec = reconcile(env)["latest_succeeded"]
    [conflict] = conflicts(env, rec["id"])
    c.post(f"{R(env)}/reconciliation/conflicts/{conflict['id']}/decision", headers=env["steward"],
           json={"decision": "MANUAL", "value": "  Mobile Data 15GB (V1) ", "rationale": "Agreed with product"})
    snap1 = c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]}).json()

    # new data for the Salesforce source (adds PRD-3) -> the old reconciliation is out of date
    u3 = load(env, "CRM_CATALOG", CRM, [["PRD-1", "Mobile Data 15GB", "Postpaid", "Active", "OFR-1"],
                                        ["PRD-2", "Mobile Data 30GB", "Postpaid", "Active", None],
                                        ["PRD-3", "Roaming Pass", "Postpaid", "Planned", None]],
              source_id=sf_src, name="salesforce-v2.xlsx")
    apply(env, u3, v1)
    ov = c.get(f"{R(env)}/reconciliation", headers=env["steward"]).json()
    assert any("New mapping applies" in b for b in ov["approval_blockers"])

    ov = reconcile(env)
    rec2 = ov["latest_succeeded"]
    [again] = conflicts(env, rec2["id"])
    assert again["status"] == "RESOLVED" and again["decision"]["decision"] == "MANUAL"  # same values: kept
    assert ov["approval_blockers"] == []
    snap2 = c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec2["id"]}).json()
    assert (snap2["code"], snap2["parent_code"]) == ("SNAP-002", "SNAP-001")
    hist = c.get(f"{R(env)}/reconciliation", headers=env["pm"]).json()["snapshots"]
    assert [(s["code"], s["status"]) for s in hist] == [("SNAP-002", "APPROVED"), ("SNAP-001", "SUPERSEDED")]

    def ids(snap):
        items = c.get(f"{R(env)}/snapshots/{snap['id']}/facts", headers=env["pm"], params={"limit": 500}).json()
        return {(x["entity"], x["key"], x["field"]): x for x in items["items"]}
    f1, f2 = ids(snap1), ids(snap2)
    key = ("Product", '["PRD-1"]', "productName")
    assert f2[key]["value"] == "Mobile Data 15GB (V1)" and f2[key]["provenance"]["rule"] == "STEWARD_MANUAL"
    assert f1[key]["evidence_id"] == f2[key]["evidence_id"]  # unchanged fact keeps its evidence id
    assert ("Product", '["PRD-3"]', "productName") in f2 and ("Product", '["PRD-3"]', "productName") not in f1


def test_manual_value_is_typed_and_defer_flags_the_fact(env):
    c = env["client"]
    two_crm_sources(env)
    rec = reconcile(env)["latest_succeeded"]
    [conflict] = conflicts(env, rec["id"])
    r = c.post(f"{R(env)}/reconciliation/conflicts/{conflict['id']}/decision", headers=env["steward"],
               json={"decision": "MANUAL", "value": "", "rationale": "x"})
    assert r.status_code == 422
    r = c.post(f"{R(env)}/reconciliation/conflicts/{conflict['id']}/decision", headers=env["steward"],
               json={"decision": "DEFER", "rationale": "Waiting for the product team"})
    assert r.json()["status"] == "DEFERRED"
    snap = c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]}).json()
    assert snap["counts"]["conflicted_facts"] == 1
    facts = c.get(f"{R(env)}/snapshots/{snap['id']}/facts", headers=env["pm"], params={"entity": "Product"}).json()
    fact = next(x for x in facts["items"] if x["key"] == '["PRD-1"]' and x["field"] == "productName")
    assert fact["value"] is None and fact["conflicted"] is True


def test_stale_source_is_flagged(env):
    old = (date.today() - timedelta(days=200)).isoformat()
    u = load(env, "CRM_CATALOG", CRM, [["PRD-1", "Mobile Data 15GB", "Postpaid", "Active", None]], as_of=old)
    apply(env, u, publish_mapping(env, "CRM_CATALOG", default_source(env, "CRM_CATALOG")))
    ov = reconcile(env)
    stale = ov["latest_succeeded"]["summary"]["stale_sources"]
    assert len(stale) == 1 and stale[0]["as_of"] == old and stale[0]["limit_days"] == 90
    future = env["client"].post(f"{R(env)}/evidence/uploads/{u}/confirm", headers=env["steward"],
                                json={"sheet": "Data", "header_row": 1,
                                      "as_of": (date.today() + timedelta(days=3)).isoformat()})
    assert future.status_code == 422


# ---- new domains, privacy, graph ----

def test_min_cohort_blocks_small_groups(env):
    load(env, "CUSTOMER_REVENUE_AGGREGATES", CRA, [["PRD-1", "2026-10-01", "2026-10-31", 5, 0, 99.5, "GBP"]])
    c = env["client"]
    m = c.post(f"{R(env)}/evidence/CUSTOMER_REVENUE_AGGREGATES/mapping", headers=env["steward"], json={}).json()
    base = f"{R(env)}/mappings/{m['mapping_id']}/versions/{m['version']}"
    v = c.post(f"{base}/validate", headers=env["steward"]).json()
    with env["sf"]() as s:
        map_service.run_validation(s, env["platform"], LIMITS, uuid.UUID(v["validation"]["id"]))
    res = c.get(base, headers=env["steward"]).json()["validation"]["result"]
    assert res["status"] == "BLOCKED", res
    assert any(i["code"] == "BELOW_MIN_COHORT" and i["layer"] == "SECURITY" for i in res["issues"]), res["issues"]


def test_telco_graph_walks_billing_and_aggregates(env):
    c = env["client"]
    loads = [
        ("CRM_CATALOG", None, CRM, [["PRD-V1", "Mobile Data 15GB V1", "Postpaid", "Active", "OFR-1"]]),
        ("ORDER_BILLING_ASSURANCE", None, OBA, [
            ["PRD-V1", "2026-10-01", "2026-10-31", 5200, 437, "BILL-A", "Main billing", "No"],
            ["PRD-V1", "2026-10-01", "2026-10-31", 5200, 437, "BILL-LEGACY", "Legacy billing", "Yes"]]),
        ("CUSTOMER_REVENUE_AGGREGATES", None, CRA, [["PRD-V1", "2026-10-01", "2026-10-31", 86214, 4209, 480000,
                                                     "GBP"]]),
    ]
    for domain, _, headers, rows in loads:
        uid = load(env, domain, headers, rows)
        apply(env, uid, publish_mapping(env, domain, default_source(env, domain)))
    rec = reconcile(env)["latest_succeeded"]
    assert rec["counts"]["records"]["BillingDependency"] == 2 and rec["counts"]["relationships"]["BILLED_BY"] == 2
    snap = c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]}).json()
    g = c.get(f"{R(env)}/snapshots/{snap['id']}/graph", headers=env["pm"], params={"product_id": "PRD-V1"}).json()
    assert g["product"]["values"]["productName"] == "Mobile Data 15GB V1"
    assert {e["name"] for e in g["edges"]} == {"BILLED_BY", "INCLUDES"}
    assert {n["key"] for n in g["nodes"] if n["entity"] == "BillingSystem"} == {'["BILL-A"]', '["BILL-LEGACY"]'}
    cra_rec = g["attached"]["CustomerRevenueAggregate"][0]
    assert cra_rec["values"]["activeSubscriptions"] == 86214 and cra_rec["values"]["mrr"] == "480000"
    assert cra_rec["evidence_ids"]["activeSubscriptions"].startswith("EV-")
    legacy = [d for d in g["attached"]["BillingDependency"] if d["values"].get("legacyBillingCode")]
    assert len(legacy) == 1 and legacy[0]["values"]["billingSystemId"] == "BILL-LEGACY"
    assert g["attached"]["OrderAssuranceAggregate"][0]["values"]["falloutOrders"] == 437
    assert c.get(f"{R(env)}/snapshots/{snap['id']}/graph", headers=env["pm"],
                 params={"product_id": "NOPE"}).status_code == 404


# ---- sources, immutability, permissions ----

def test_add_source_rules(env):
    c = env["client"]
    add_source(env, "CRM_CATALOG", "Dynamics export", "DYNAMICS")  # also creates the default Excel source first
    dup = c.post(f"{R(env)}/evidence/CRM_CATALOG/sources", headers=env["steward"],
                 json={"name": "dynamics EXPORT", "system": "OTHER"})
    assert dup.status_code == 409
    bad = c.post(f"{R(env)}/evidence/CRM_CATALOG/sources", headers=env["steward"], json={"name": "x", "system": "a b"})
    assert bad.status_code == 422 and {e["field"] for e in bad.json()["detail"]["errors"]} == {"name", "system"}
    assert c.post(f"{R(env)}/evidence/CRM_CATALOG/sources", headers=env["viewer"],
                  json={"name": "Viewer source", "system": "X1"}).status_code == 403
    ov = c.get(f"{R(env)}/evidence", headers=env["pm"]).json()
    crm = next(d for d in ov["domains"] if d["key"] == "CRM_CATALOG")
    assert [s["system"] for s in crm["sources"]] == ["EXCEL", "DYNAMICS"]


def test_snapshots_are_immutable(env):
    two_crm_sources(env)
    rec = reconcile(env)["latest_succeeded"]
    [conflict] = conflicts(env, rec["id"])
    env["client"].post(f"{R(env)}/reconciliation/conflicts/{conflict['id']}/decision", headers=env["steward"],
                       json={"decision": "SELECT_SOURCE", "source_id": conflict["recommended_source_id"]})
    snap = env["client"].post(f"{R(env)}/snapshots", headers=env["steward"],
                              json={"reconciliation_id": rec["id"]}).json()
    for sql in ("UPDATE rationalisation.evidence_snapshot SET content_hash = 'sha256:' || repeat('0', 64) "
                "WHERE id = :id",
                "DELETE FROM rationalisation.evidence_snapshot WHERE id = :id",
                "UPDATE rationalisation.snapshot_fact SET value = '\"x\"' WHERE snapshot_id = :id",
                "DELETE FROM rationalisation.snapshot_relationship WHERE snapshot_id = :id"):
        with env["sf"]() as s, pytest.raises(DBAPIError):
            s.execute(text(sql), {"id": snap["id"]})
            s.commit()
    with env["sf"]() as s, pytest.raises(DBAPIError, match="append-only"):
        s.execute(text("DELETE FROM rationalisation.steward_decision WHERE run_id = :r"), {"r": env["run"]["id"]})
        s.commit()


def test_permissions_and_tenancy(env, login):
    c = env["client"]
    two_crm_sources(env)
    assert c.post(f"{R(env)}/reconciliation", headers=env["pm"]).status_code == 403
    rec = reconcile(env)["latest_succeeded"]
    [conflict] = conflicts(env, rec["id"])
    assert c.post(f"{R(env)}/reconciliation/conflicts/{conflict['id']}/decision", headers=env["pm"],
                  json={"decision": "DEFER", "rationale": "nope"}).status_code == 403
    assert c.post(f"{R(env)}/snapshots", headers=env["admin"],
                  json={"reconciliation_id": rec["id"]}).status_code == 403
    assert c.get(f"{R(env)}/reconciliation", headers=env["viewer"]).status_code == 200
    other = login("data_steward", "t-other-reconcile")
    assert c.get(f"{R(env)}/reconciliation", headers=other).status_code == 404
    assert c.post(f"{R(env)}/reconciliation/conflicts/{conflict['id']}/decision", headers=other,
                  json={"decision": "DEFER", "rationale": "nope"}).status_code == 404


def test_reconcile_needs_applied_data(env):
    r = env["client"].post(f"{R(env)}/reconciliation", headers=env["steward"])
    assert r.status_code == 409 and r.json()["detail"]["code"] == "conflict"
