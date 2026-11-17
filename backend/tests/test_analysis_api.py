"""Step 10 end to end: start a run on the sample evidence, run the analysis workflow, check source products, findings,
candidates, batch counts, reproducibility and supersession."""

import uuid
from pathlib import Path

import pytest
from openpyxl import load_workbook
from sqlalchemy import select, text
from sqlalchemy.exc import DBAPIError

from app.db.models import AnalysisRun
from app.services import analysis as analysis_service
from tests.test_reconciliation_api import (  # noqa: F401 - env is a fixture
    R,
    apply,
    default_source,
    env,
    load,
    publish_mapping,
    reconcile,
)

SAMPLES = Path(__file__).resolve().parents[2] / "docs" / "samples"
FILES = {
    "CRM_CATALOG": "CRM_Catalog_sample.xlsx", "CPQ": "CPQ_Rules_sample.xlsx", "PRICE_BOOK": "Price_Book_sample.xlsx",
    "LIFECYCLE": "Lifecycle_sample.xlsx", "TECHNICAL_CATALOG": "Technical_Catalog_sample.xlsx",
    "PROVISIONING": "Provisioning_sample.xlsx", "INVENTORY": "Inventory_sample.xlsx",
    "ORDER_BILLING_ASSURANCE": "Order_Billing_Assurance_sample.xlsx",
    "CUSTOMER_REVENUE_AGGREGATES": "Customer_Revenue_sample.xlsx",
}


def sample_rows(name):
    ws = load_workbook(SAMPLES / name)["Data"]
    rows = [list(r) for r in ws.iter_rows(values_only=True)]
    return [str(h) for h in rows[0]], [[None if v is None else (v if isinstance(v, int | float) else str(v))
                                        for v in r] for r in rows[1:]]


def started_run(env, files=None):
    files = files or FILES
    c = env["client"]
    ov = c.get(f"{R(env)}/evidence", headers=env["pm"]).json()
    assert c.put(f"{R(env)}/evidence/domains", headers=env["pm"],
                 json={"domains": list(FILES), "version": ov["version"]}).status_code == 200
    for domain, name in files.items():
        headers, rows = sample_rows(name)
        uid = load(env, domain, headers, rows, name=name)
        apply(env, uid, publish_mapping(env, domain, default_source(env, domain)))
    rec = reconcile(env)["latest_succeeded"]
    assert c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]}).status_code == 201
    cfg = c.get(f"{R(env)}/configuration", headers=env["pm"]).json()
    assert cfg["status"] != "BLOCKED", cfg["issues"]
    if cfg["unacknowledged"]:
        cfg = c.post(f"{R(env)}/configuration/acknowledgements", headers=env["pm"],
                     json={"version": cfg["version"], "warning_ids": cfg["unacknowledged"]}).json()
    r = c.post(f"{R(env)}/start", headers=env["pm"], json={"version": cfg["version"]})
    assert r.status_code == 201, r.text


def run_latest(env):
    with env["sf"]() as s:
        a = s.scalar(select(AnalysisRun).where(AnalysisRun.run_id == uuid.UUID(env["run"]["id"]))
                     .order_by(AnalysisRun.seq.desc()))
        analysis_service.run_analysis(s, env["platform"], a.id)


@pytest.fixture
def analysed(env):
    started_run(env)
    a = env["client"].get(f"{R(env)}/analysis", headers=env["pm"]).json()
    assert a["status"] == "QUEUED" and a["reason"] == "START"  # Start queued the workflow
    run_latest(env)
    return env


def test_analysis_workflow(analysed, db):
    env, c = analysed, analysed["client"]
    a = c.get(f"{R(env)}/analysis", headers=env["pm"]).json()
    assert a["status"] == "SUCCEEDED", a["error"]
    assert [(s["stage"], s["status"]) for s in a["stages"]] == [
        ("Discover", "COMPLETE"), ("Normalize", "COMPLETE"), ("Analyze", "COMPLETE"), ("Assess", "COMPLETE"),
        ("Recommend", "COMPLETE"), ("Review", "WAITING"), ("Approve", "NOT_STARTED")]
    sp = a["source_products"]
    assert (sp["snapshotProducts"], sp["sourceProducts"]) == (8, 5)  # POSTPAID incl. SIM-only; 3 other families
    assert sp["excludedProducts"]["outOfScopeFamily"] == ["PRD-1005", "PRD-1006", "PRD-1008"]
    assert a["detector_summary"] == {"rawFindings": 6, "duplicate": 1, "overlap": 3, "legacy": 2, "complexity": 0,
                                     "operational_burden": 0}
    assert a["counts"] == {"rawFindings": 6, "merged": 0, "invalid": 0, "outOfScope": 0, "superseded": 0,
                           "candidates": 6}
    assert c.get(f"{R(env)}", headers=env["pm"]).json()["state"] == "REVIEW"

    findings = c.get(f"{R(env)}/findings", headers=env["pm"], params={"type": "DUPLICATE"}).json()
    [dup] = findings
    assert dup["subjects"] == ["PRD-V1", "PRD-V2"] and dup["band"] == "PROBABLE" and dup["signal"] == 850
    assert dup["factors"]["technical"]["value"] == 0 and all(e.startswith("EV-") for e in dup["evidence_ids"])
    legacy = c.get(f"{R(env)}/findings", headers=env["pm"], params={"type": "LEGACY"}).json()
    assert {tuple(f["subjects"]) for f in legacy} == {("PRD-1007",), ("PRD-V1",)}
    assert any("1240 active services" in n for f in legacy for n in f["notes"])
    assert c.get(f"{R(env)}/findings", headers=env["viewer"]).status_code == 403

    with pytest.raises(DBAPIError):
        db.execute(text("UPDATE rationalisation.detector_finding SET signal = 0"))
    db.rollback()
    assert c.post(f"{R(env)}/analysis/retry", headers=env["pm"]).status_code == 409


def test_same_snapshot_and_packs_reproduce_and_supersede(analysed):
    env, c = analysed, analysed["client"]
    first = c.get(f"{R(env)}/analysis", headers=env["pm"]).json()
    with env["sf"]() as s:
        from app.db.models import Run
        run = s.get(Run, uuid.UUID(env["run"]["id"]))
        run.state = "ANALYSING"
        analysis_service.request_analysis(s, env["platform"], run, "test", reason="REASSESSMENT")
    run_latest(env)
    second = c.get(f"{R(env)}/analysis", headers=env["pm"]).json()
    assert second["seq"] == 2 and second["result_hash"] == first["result_hash"]
    assert second["counts"]["superseded"] == 6
    with env["sf"]() as s:
        from app.db.models import Candidate
        rows = list(s.scalars(select(Candidate).where(Candidate.run_id == uuid.UUID(env["run"]["id"]))))
    old = [r for r in rows if r.status == "SUPERSEDED"]
    new = {r.id: r for r in rows if r.status == "ACTIVE"}
    assert len(old) == 6 and len(new) == 6
    assert all(new[o.superseded_by].code == o.code for o in old)  # codes stay stable across reassessments


def test_failed_analysis_can_be_retried(env):
    started_run(env)
    with env["sf"]() as s:
        a = s.scalar(select(AnalysisRun).where(AnalysisRun.run_id == uuid.UUID(env["run"]["id"])))
        orig = analysis_service.discover
        analysis_service.discover = lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom"))
        try:
            analysis_service.run_analysis(s, env["platform"], a.id)
        finally:
            analysis_service.discover = orig
    a = env["client"].get(f"{R(env)}/analysis", headers=env["pm"]).json()
    assert a["status"] == "FAILED" and a["stages"][0]["status"] == "FAILED" and "boom" in a["error"]
    assert env["client"].get(f"{R(env)}", headers=env["pm"]).json()["state"] == "FAILED"
    r = env["client"].post(f"{R(env)}/analysis/retry", headers=env["pm"])
    assert r.status_code == 202 and r.json()["seq"] == 2
    run_latest(env)
    assert env["client"].get(f"{R(env)}/analysis", headers=env["pm"]).json()["status"] == "SUCCEEDED"
