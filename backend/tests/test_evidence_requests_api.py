"""Step 13 end to end: the CAND-1042 gap branch. Provisioning evidence is missing -> technical NOT_ASSESSED ->
evidence blocker -> evidence request ER-101 (contract, assign, start) -> the return arrives through the Evidence Hub
as child snapshot SNAP-002 -> RECEIVED -> accept -> targeted reassessment with the pinned manifest -> blocker
resolved, request done, decision readiness re-evaluated."""

import uuid

from sqlalchemy import select

from app.db.models import Blocker, Run
from tests.test_analysis_api import FILES, run_latest, sample_rows, started_run
from tests.test_assessment_api import by_subjects, candidates, set_objectives
from tests.test_reconciliation_api import R, apply, default_source, env, load, publish_mapping, reconcile  # noqa: F401

API = "/api/rationalisation"


def gap_run(env):
    set_objectives(env, ["REDUCE_DUPLICATES", "BSS_SUNSET"])
    started_run(env, {k: v for k, v in FILES.items() if k != "PROVISIONING"})
    run_latest(env)
    return by_subjects(candidates(env), ["PRD-V1", "PRD-V2"])


def return_provisioning(env):
    """The Excel aggregate return: upload, map, apply, reconcile, approve the child snapshot."""
    c = env["client"]
    headers, rows = sample_rows(FILES["PROVISIONING"])
    uid = load(env, "PROVISIONING", headers, rows, name=FILES["PROVISIONING"])
    if "_provisioning_mapping" not in env:  # the published mapping is reused by later returns of the domain
        env["_provisioning_mapping"] = publish_mapping(env, "PROVISIONING", default_source(env, "PROVISIONING"))
    apply(env, uid, env["_provisioning_mapping"])
    rec = reconcile(env)["latest_succeeded"]
    r = c.post(f"{R(env)}/snapshots", headers=env["steward"], json={"reconciliation_id": rec["id"]})
    assert r.status_code == 201, r.text
    return r.json()


def test_gap_branch_end_to_end(env):
    c = env["client"]
    dup = gap_run(env)
    base = f"{R(env)}/evidence-requests"
    d = c.get(f"{R(env)}/candidates/{dup.code}", headers=env["pm"]).json()
    assert d["status"] == "EVIDENCE_GAP" and d["openEvidenceNeeds"] == 0 and d["evidenceRequests"] == []
    assert d["decisionReadiness"]["status"] == "BLOCKED"
    with env["sf"]() as s:
        before = s.get(Run, uuid.UUID(env["run"]["id"])).state
    assert before == "REVIEW"
    # evidence is locked while in review without an open request
    locked = c.post(f"{R(env)}/evidence/PROVISIONING/sources", headers=env["steward"],
                    json={"name": "Late source", "system": "OSS"})
    assert locked.status_code == 409 and locked.json()["detail"]["code"] == "not_editable"

    # Request evidence from Candidate Review (user button, no LLM): one request per gap, DRAFT
    r = c.post(f"{R(env)}/candidates/{dup.code}/evidence-requests", headers=env["pm"])
    assert r.status_code == 200, r.text
    reqs = r.json()
    assert [x["code"] for x in reqs] == ["ER-101"] and reqs[0]["status"] == "DRAFT"
    er = reqs[0]
    assert er["domain"] == "PROVISIONING" and er["action"] == "CONSOLIDATE" and er["consequence"] == "BLOCKED"
    assert er["trigger"] == f"{dup.code} · Consolidate" and er["decisionImpact"] == "Technical equivalence not assessed"
    assert er["contract"]["grouping"] == "Product ID + Service Specification ID"
    assert er["contract"]["products"] == ["PRD-V1", "PRD-V2"] and er["blockerCode"].startswith("BLK-")
    assert er["snapshot"]["code"] == "SNAP-001" and er["ownerRole"] == "enterprise_architect"
    # idempotent: asking again opens the existing request
    assert [x["code"] for x in c.post(f"{R(env)}/candidates/{dup.code}/evidence-requests", headers=env["pm"]).json()] == ["ER-101"]
    assert c.get(f"{R(env)}/candidates/{dup.code}", headers=env["pm"]).json()["openEvidenceNeeds"] == 1

    q = c.get(base, headers=env["pm"]).json()
    assert q["metrics"] == {"open": 1, "openCandidates": 1, "blocking": 1, "awaiting": 0, "ready": 0, "reassessing": 0}
    assert c.get(f"{API}/evidence-requests", headers=env["pm"], params={"status": "draft"}).json()["metrics"]["open"] == 1

    # assign: owner, due date and channel only; the contract is not editable
    bad = c.post(f"{base}/ER-101/assign", headers=env["pm"], json={"fulfilmentMethod": "FTP"})
    assert bad.status_code == 422
    a = c.post(f"{base}/ER-101/assign", headers=env["pm"],
               json={"ownerId": "provisioning.owner", "dueDate": "2030-01-31", "fulfilmentMethod": "excel_aggregate"})
    assert a.status_code == 200, a.text
    assert a.json()["status"] == "OPEN" and a.json()["ownerId"] == "provisioning.owner"
    assert a.json()["fulfilmentMethod"] == "EXCEL_AGGREGATE" and [h["to"] for h in a.json()["history"]] == ["DRAFT", "OPEN"]
    assert c.post(f"{base}/ER-101/accept", headers=env["steward"]).status_code == 409  # nothing received yet

    s_ = c.post(f"{base}/ER-101/start", headers=env["steward"])
    assert s_.status_code == 200, s_.text
    job = s_.json()["fulfilmentJob"]
    assert s_.json()["status"] == "IN_PROGRESS" and job["channel"] == "EXCEL_AGGREGATE"
    assert job["uploadPath"].endswith("/evidence/PROVISIONING/uploads") and job["template"]
    assert c.get(base, headers=env["pm"]).json()["metrics"]["awaiting"] == 1

    # the return arrives through the Evidence Hub (unlocked by the open request) as a child snapshot
    snap = return_provisioning(env)
    assert snap["code"] == "SNAP-002" and snap["parent_code"] == "SNAP-001"
    got = c.get(f"{base}/ER-101", headers=env["pm"]).json()
    assert got["status"] == "RECEIVED" and got["receivedSnapshot"]["code"] == "SNAP-002"
    assert c.get(base, headers=env["pm"]).json()["metrics"]["ready"] == 1

    # accept -> reassessment on SNAP-002 with the pinned manifest
    assert c.post(f"{base}/ER-101/accept", headers=env["pm"]).status_code == 403  # needs snapshot.approve
    acc = c.post(f"{base}/ER-101/accept", headers=env["steward"])
    assert acc.status_code == 200, acc.text
    assert acc.json()["accepted"] == ["ER-101"] and acc.json()["snapshot"] == "SNAP-002"
    an = c.get(f"{R(env)}/analysis", headers=env["pm"]).json()
    assert an["status"] == "QUEUED" and an["reason"] == "EVIDENCE_ACCEPTED" and an["seq"] == 2
    assert c.get(f"{base}/ER-101", headers=env["pm"]).json()["reassessmentStatus"] == "QUEUED"
    run_latest(env)

    after = c.get(f"{R(env)}/candidates/{dup.code}", headers=env["pm"]).json()
    assert after["snapshot"]["code"] == "SNAP-002" and after["candidateId"] == dup.code  # identity kept
    assert after["dimensions"]["TECHNICAL"]["status"] == "ASSESSED" and after["status"] != "EVIDENCE_GAP"
    assert after["recommendation"]["preliminary"] is False and after["openEvidenceNeeds"] == 0
    assert after["recommendation"]["action"] == "MIGRATE"  # technically different: rule 4
    rd = c.post(f"{R(env)}/candidates/{dup.code}/decision-readiness", headers=env["pm"],
                json={"action": "MIGRATE", "analysisId": after["analysisId"]}).json()
    assert rd["status"] == "PASS" and rd["continueToDecisionEnabled"] is True
    done = c.get(f"{base}/ER-101", headers=env["pm"]).json()
    assert done["status"] == "ACCEPTED" and done["reassessmentStatus"] == "DONE"
    assert done["acceptedSnapshot"]["code"] == "SNAP-002"
    with env["sf"]() as s:
        b = s.scalar(select(Blocker).where(Blocker.run_id == uuid.UUID(env["run"]["id"]),
                                           Blocker.code == er["blockerCode"]))
        assert b.state == "RESOLVED" and s.get(Run, uuid.UUID(env["run"]["id"])).state == "REVIEW"
    ov = c.get(f"{R(env)}/overview", headers=env["pm"]).json()
    assert ov["snapshot"]["code"] == "SNAP-002" and ov["metrics"]["blocked"] == 0
    assert c.get(base, headers=env["pm"]).json()["metrics"]["open"] == 0


def test_reject_then_corrected_return_and_detect_all(env):
    c = env["client"]
    dup = gap_run(env)
    base = f"{R(env)}/evidence-requests"
    created = c.post(f"{base}/detect", headers=env["pm"]).json()
    assert any(x["candidateCode"] == dup.code and x["domain"] == "PROVISIONING" for x in created)
    codes = sorted(x["code"] for x in created)
    assert codes[0] == "ER-101" and len(codes) == len(set(codes))
    mine = next(x for x in created if x["candidateCode"] == dup.code and x["domain"] == "PROVISIONING")
    assert c.post(f"{base}/{mine['code']}/assign", headers=env["pm"], json={"dueDate": "2000-01-01"}).status_code == 422
    assert c.post(f"{base}/{mine['code']}/assign", headers=env["pm"], json={}).json()["status"] == "OPEN"
    snap = return_provisioning(env)
    assert c.get(f"{base}/{mine['code']}", headers=env["pm"]).json()["status"] == "RECEIVED"
    # every open request of the same domain received the same return
    received = [x for x in c.get(base, headers=env["pm"], params={"status": "RECEIVED"}).json()["items"]]
    assert all(x["receivedSnapshot"]["code"] == snap["code"] for x in received) and len(received) >= 1

    rj = c.post(f"{base}/{mine['code']}/reject", headers=env["steward"], json={"reason": "   "})
    assert rj.status_code == 422
    rj = c.post(f"{base}/{mine['code']}/reject", headers=env["steward"], json={"reason": "Wrong cohort"})
    assert rj.status_code == 200 and rj.json()["status"] == "REJECTED" and rj.json()["statusReason"] == "Wrong cohort"
    assert rj.json()["receivedSnapshot"] is None
    # the same return is not received again; a corrected one is
    snap2 = return_provisioning(env)
    assert snap2["code"] == "SNAP-003"
    again = c.get(f"{base}/{mine['code']}", headers=env["pm"]).json()
    assert again["status"] == "RECEIVED" and again["receivedSnapshot"]["code"] == "SNAP-003"
    hist = [h["to"] for h in again["history"]]
    assert hist == ["DRAFT", "OPEN", "RECEIVED", "REJECTED", "RECEIVED"]


def test_permissions_and_tenancy(env, login):
    c = env["client"]
    gap_run(env)
    base = f"{R(env)}/evidence-requests"
    assert c.post(f"{base}/detect", headers=env["viewer"]).status_code == 403
    assert c.get(base, headers=env["viewer"]).status_code == 403
    other = login("portfolio_manager", "t-other-er")
    assert c.get(base, headers=other).status_code == 404
    assert c.get(f"{API}/evidence-requests", headers=other).json()["items"] == []
    assert c.get(f"{base}/ER-999", headers=env["pm"]).status_code == 404
