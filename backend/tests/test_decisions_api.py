"""Step 14 end to end (CAND-1042 pattern): decision versions, submission gating (rules 5 and 6), policy-based
routing, approve / return / hold / reject with comments, self-approval control, audit evidence, and the gap branch:
submission blocked before the evidence return, a decision taken before a reassessment is superseded."""

import uuid

from sqlalchemy import select

from app.db.models import ApprovalAction, Candidate, DecisionVersion
from tests.test_analysis_api import run_latest
from tests.test_candidates_api import analysed
from tests.test_evidence_requests_api import gap_run, return_provisioning
from tests.test_reconciliation_api import R, env  # noqa: F401 - env is a fixture

API = "/api/rationalisation"
RATIONALE = ("Migrate V1 into V2 because the products are commercially equivalent but technically different; "
             "preserve contracted price until renewal and remove the legacy billing code after the final cohort.")


def body(**over):
    base = {"disposition": "MIGRATE", "targetProductId": "PRD-V2", "effectiveDate": "2027-03-31",
            "ownerId": "a.patel", "rationale": RATIONALE}
    base.update(over)
    return base


def test_decision_versions_submission_and_full_approval(env, login):
    c = env["client"]
    dup = analysed(env)
    url = f"{R(env)}/candidates/{dup.code}/decision"
    assert c.get(url, headers=env["pm"]).json() is None
    bad = c.post(url, headers=env["pm"], json=body(rationale="short", effectiveDate="2020-01-01"))
    assert bad.status_code == 422 and {e["field"] for e in bad.json()["detail"]["errors"]} == {"rationale", "effectiveDate"}
    assert c.post(url, headers=env["pm"], json=body(disposition="RETIRE")).status_code == 422  # not an option
    assert c.post(url, headers=env["pm"], json=body(disposition="CONSOLIDATE")).status_code == 201  # draft only

    d1 = c.post(url, headers=env["pm"], json=body()).json()
    assert d1["code"] == "DEC-0001" and d1["state"] == "DRAFT" and d1["currentVersionNo"] == 2
    cur = d1["current"]
    assert cur["contentHash"].startswith("sha256:") and cur["snapshotCode"] == "SNAP-001"
    assert cur["conditions"][0] == "PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL"
    assert cur["commercialTreatment"] == "Grandfather price until renewal" and cur["targetName"]
    assert [v["versionNo"] for v in d1["versions"]] == [1, 2]  # every save is a new immutable version
    assert c.get(f"{R(env)}/candidates/{dup.code}", headers=env["pm"]).json()["status"] == "DECISION"

    # submit: wrong hash refused; rule 6 passes on the complete snapshot; route from the policy pack
    stale = c.post(f"{url}/submit", headers=env["pm"], json={"versionId": cur["id"], "contentHash": "sha256:nope"})
    assert stale.status_code == 409
    old = c.post(f"{url}/submit", headers=env["pm"], json={"versionId": d1["versions"][0]["id"]})
    assert old.status_code == 409
    sub = c.post(f"{url}/submit", headers=env["pm"], json={"versionId": cur["id"], "contentHash": cur["contentHash"]})
    assert sub.status_code == 200, sub.text
    s = sub.json()
    assert s["state"] == "SUBMITTED" and sorted(t["scope"] for t in s["tasks"]) == ["architecture", "commercial", "product"]
    assert s["overall"] == "PASS_WITH_WARNING"
    assert next(x for x in s["policyChecks"] if x["key"] == "customerNotice")["detail"] == "30-day notice for 4,209 contracts"
    assert c.get(f"{R(env)}/candidates/{dup.code}", headers=env["pm"]).json()["status"] == "APPROVAL"
    assert c.post(url, headers=env["pm"], json=body()).status_code == 409  # locked while in approval
    assert c.get(f"{API}/runs/summary", headers=env["pm"]).json()["awaiting_approval"] == 1

    # approvers act on the exact version; the PM (submitter) cannot self-approve; comments are required
    code = s["code"]
    act = f"{R(env)}/decisions/{code}/actions"
    assert c.post(act, headers=env["pm"], json={"action": "APPROVE"}).status_code == 403  # no approval.act
    product = login("product_approver", env["tenant"])
    commercial = login("commercial_approver", env["tenant"])
    architect = login("architecture_approver", env["tenant"])
    assert c.get(f"{API}/approvals", headers=product).json()[0]["code"] == code
    assert c.get(f"{API}/approvals", headers=env["pm"]).json() == []  # no scope
    hold = c.post(act, headers=product, json={"action": "HOLD"})
    assert hold.status_code == 422 and "comment" in hold.json()["detail"]["errors"][0]["message"]
    held = c.post(act, headers=product, json={"action": "HOLD", "comment": "Waiting for the notice plan",
                                              "contentHash": cur["contentHash"]}).json()
    assert held["state"] == "ON_HOLD"
    hashed = c.post(act, headers=product, json={"action": "APPROVE", "contentHash": "sha256:other"})
    assert hashed.status_code == 422
    assert c.post(act, headers=product, json={"action": "APPROVE"}).json()["state"] == "SUBMITTED"
    twice = c.post(act, headers=product, json={"action": "APPROVE"})
    assert twice.status_code == 422 and "already acted" in twice.json()["detail"]["errors"][0]["message"]
    assert c.post(act, headers=commercial, json={"action": "APPROVE"}).json()["state"] == "SUBMITTED"
    final = c.post(act, headers=architect, json={"action": "APPROVE"}).json()
    assert final["state"] == "APPROVED" and final["approvedVersionId"] == cur["id"]
    assert [a["action"] for a in final["actions"]] == ["SUBMIT", "HOLD", "APPROVE", "APPROVE", "APPROVE"]
    assert c.get(f"{R(env)}/candidates/{dup.code}", headers=env["pm"]).json()["status"] == "APPROVED"
    assert c.post(act, headers=architect, json={"action": "REJECT", "comment": "x"}).status_code == 409
    with env["sf"]() as s_:
        rows = list(s_.scalars(select(ApprovalAction).where(ApprovalAction.decision_id == uuid.UUID(final["id"]))))
        assert all(r.content_hash == cur["contentHash"] and r.auth_context is not None for r in rows)
        assert {r.actor_id for r in rows if r.action == "APPROVE"} and all(r.actor_roles for r in rows)
        assert len(list(s_.scalars(select(DecisionVersion).where(DecisionVersion.decision_id == uuid.UUID(final["id"]))))) == 2


def test_return_and_reject_paths(env, login):
    c = env["client"]
    dup = analysed(env)
    url = f"{R(env)}/candidates/{dup.code}/decision"
    d = c.post(url, headers=env["pm"], json=body(disposition="RETAIN", targetProductId=None)).json()
    d = c.post(f"{url}/submit", headers=env["pm"], json={"versionId": d["current"]["id"]}).json()
    assert [t["scope"] for t in d["tasks"]] == ["product", "commercial"]  # 86,214 subscribers escalate RETAIN
    product = login("product_approver", env["tenant"])
    returned = c.post(f"{R(env)}/decisions/{d['code']}/actions", headers=product,
                      json={"action": "RETURN", "comment": "Add the renewal date"}).json()
    assert returned["state"] == "RETURNED" and {t["state"] for t in returned["tasks"]} == {"RETURNED", "CANCELLED"}
    assert c.get(f"{R(env)}/candidates/{dup.code}", headers=env["pm"]).json()["status"] == "DECISION"
    d2 = c.post(url, headers=env["pm"], json=body(disposition="RETAIN", targetProductId=None,
                                                  rationale=RATIONALE + " Renewal 2027.")).json()
    assert d2["currentVersionNo"] == 2 and d2["state"] == "DRAFT"
    d2 = c.post(f"{url}/submit", headers=env["pm"], json={"versionId": d2["current"]["id"]}).json()
    rejected = c.post(f"{R(env)}/decisions/{d2['code']}/actions", headers=product,
                      json={"action": "REJECT", "comment": "Not viable"}).json()
    assert rejected["state"] == "REJECTED"
    assert c.get(f"{API}/approvals", headers=product).json() == []


def test_gap_branch_blocks_submission_then_reassessment_supersedes(env, login):
    c = env["client"]
    dup = gap_run(env)
    url = f"{R(env)}/candidates/{dup.code}/decision"
    d = c.post(url, headers=env["pm"], json=body(disposition="CONSOLIDATE")).json()
    blocked = c.post(f"{url}/submit", headers=env["pm"], json={"versionId": d["current"]["id"]})
    assert blocked.status_code == 409 and blocked.json()["detail"]["code"] == "submission_blocked"
    assert any("PROVISIONING" in e["message"] or "Technical" in e["message"] for e in blocked.json()["detail"]["errors"])
    # rule 5: INVESTIGATE can always be submitted
    inv = c.post(url, headers=env["pm"], json=body(disposition="INVESTIGATE", targetProductId=None)).json()
    sub = c.post(f"{url}/submit", headers=env["pm"], json={"versionId": inv["current"]["id"]}).json()
    assert sub["state"] == "SUBMITTED" and [t["scope"] for t in sub["tasks"]] == ["product", "commercial"]

    # the evidence return and reassessment materially change the picture -> superseded, new version needed
    c.post(f"{R(env)}/candidates/{dup.code}/evidence-requests", headers=env["pm"])
    c.post(f"{R(env)}/evidence-requests/ER-101/assign", headers=env["pm"], json={})
    return_provisioning(env)
    assert c.post(f"{R(env)}/evidence-requests/ER-101/accept", headers=env["steward"]).status_code == 200
    run_latest(env)
    after = c.get(f"{R(env)}/decisions/{sub['code']}", headers=env["pm"]).json()
    assert after["state"] == "SUPERSEDED" and after["actions"][-1]["action"] == "SUPERSEDE"
    assert "Recommendation changed" in after["actions"][-1]["comment"]
    assert all(t["state"] == "CANCELLED" for t in after["tasks"])
    with env["sf"]() as s_:
        row = s_.scalar(select(Candidate).where(Candidate.run_id == uuid.UUID(env["run"]["id"]),
                                                Candidate.code == dup.code, Candidate.status == "ACTIVE"))
        assert row.candidate_status == "REVIEW"
    # a new version on the reassessed candidate submits and routes fully
    d3 = c.post(url, headers=env["pm"], json=body()).json()
    assert d3["currentVersionNo"] == 3 and d3["current"]["snapshotCode"] == "SNAP-002"
    s3 = c.post(f"{url}/submit", headers=env["pm"], json={"versionId": d3["current"]["id"]}).json()
    assert s3["state"] == "SUBMITTED" and s3["overall"] == "PASS_WITH_WARNING"
