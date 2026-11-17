"""Step 7 end to end: evidence readiness from the approved snapshot or a preview of the latest reconciliation."""

from datetime import date, timedelta

from tests.test_reconciliation_api import (  # noqa: F401 - env is a fixture
    CRA,
    CRM,
    OBA,
    R,
    apply,
    default_source,
    env,
    load,
    publish_mapping,
    reconcile,
)


def readiness(env, source=None, headers=None):
    params = {"source": source} if source else {}
    return env["client"].get(f"{R(env)}/evidence-readiness", headers=headers or env["pm"], params=params)


def loaded(env, domain, headers, rows, **kw):
    uid = load(env, domain, headers, rows, **kw)
    apply(env, uid, publish_mapping(env, domain, default_source(env, domain)))


def domain(body, name):
    return next(d for d in body["domains"] if d["domain"] == name)


def test_readiness_follows_preview_then_snapshot(env):
    c = env["client"]
    empty = readiness(env)
    assert empty.status_code == 200, empty.text
    body = empty.json()
    assert body["based_on"]["kind"] == "none" and body["policy_ref"] == "TELCO-READINESS@1.0"
    assert body["kpis"]["products"] == 0 and body["kpis"]["overall_completeness"] == 0
    assert all(d["status"] == "NOT_ASSESSED" for d in body["domains"])

    loaded(env, "CRM_CATALOG", CRM, [["PRD-1", "Mobile Data 15GB", "Postpaid", "Active", None],
                                     ["PRD-2", "Mobile Data 30GB", "Postpaid", "Active", None]])
    loaded(env, "CUSTOMER_REVENUE_AGGREGATES", CRA, [["PRD-1", "2026-10-01", "2026-10-31", 86214, 4209, 480000,
                                                       "GBP"]])
    loaded(env, "ORDER_BILLING_ASSURANCE", OBA, [["PRD-1", "2026-10-01", "2026-10-31", 5200, 437, "BILL-A",
                                                  "Main billing", "No"]])
    rec = reconcile(env)["latest_succeeded"]

    assert readiness(env).json()["based_on"]["kind"] == "none"  # nothing approved yet
    prev = readiness(env, "preview").json()
    assert prev["based_on"]["kind"] == "preview" and prev["based_on"]["reconciliation_id"] == rec["id"]
    crm, cra, oba = (domain(prev, d) for d in ("CRM_CATALOG", "CUSTOMER_REVENUE_AGGREGATES",
                                               "ORDER_BILLING_ASSURANCE"))
    assert (crm["status"], crm["coverage"]) == ("AVAILABLE", 100)
    assert (cra["status"], cra["coverage"], cra["covered"], cra["total"]) == ("PARTIAL", 50, 1, 2)
    assert cra["consequence"] == "Customer impact unknown; retire/migrate approval blocked for 1 of 2 products."
    assert (oba["status"], oba["coverage"]) == ("PARTIAL", 50)
    assert prev["kpis"]["products"] == 2

    snap = c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]}).json()
    body = readiness(env).json()
    assert body["based_on"] == {"kind": "snapshot", "snapshot_id": snap["id"], "code": "SNAP-001",
                                "label": "Based on SNAP-001", "content_hash": snap["content_hash"],
                                "at": body["based_on"]["at"]}
    assert body["domains"] == prev["domains"] and body["result_hash"] == prev["result_hash"]
    retain = next(d for d in body["dispositions"] if d["disposition"] == "RETAIN")
    assert retain["ready"] is True
    retire = next(d for d in body["dispositions"] if d["disposition"] == "RETIRE")
    assert retire["ready"] is False and {"domain": "CUSTOMER_REVENUE_AGGREGATES", "status": "PARTIAL"} in \
        retire["missing"]
    assert readiness(env).json()["result_hash"] == body["result_hash"]  # reproducible


def test_stale_critical_evidence_blocks(env):
    old = (date.today() - timedelta(days=200)).isoformat()
    loaded(env, "CRM_CATALOG", CRM, [["PRD-1", "Mobile Data 15GB", "Postpaid", "Active", None]], as_of=old)
    reconcile(env)
    crm = domain(readiness(env, "preview").json(), "CRM_CATALOG")
    assert (crm["status"], crm["stale"], crm["coverage"]) == ("BLOCKED", True, 100)


def test_permissions_tenancy_and_bad_source(env, login):
    assert readiness(env, headers=env["viewer"]).status_code == 200
    assert readiness(env, "everything").status_code == 422
    other = login("portfolio_manager", "t-other-readiness")
    r = readiness(env, headers=other)
    assert r.status_code == 404 and r.json()["detail"]["code"] == "not_found"
    assert env["client"].get(f"{R(env)}/evidence-readiness").status_code == 401


def test_readiness_policy_is_in_the_registry(env):
    r = env["client"].get("/api/rationalisation/registries/readiness-policies", headers=env["steward"])
    assert r.status_code == 200, r.text
    assert [a["key"] for a in r.json()["items"] if a["key"] == "TELCO-READINESS"] == ["TELCO-READINESS"]
