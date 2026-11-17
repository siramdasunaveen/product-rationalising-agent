"""Step 11 end to end: assessment on the sample evidence, the CAND-1042-style evidence gap branch, blocker records
and the Run Overview read model."""

import uuid

from sqlalchemy import select

from app.db.models import Blocker, Candidate, Run
from app.services import analysis as analysis_service
from app.services import assessment as assessment_service
from tests.test_analysis_api import FILES, run_latest, started_run
from tests.test_reconciliation_api import R, env  # noqa: F401 - env is a fixture


def candidates(env):
    with env["sf"]() as s:
        a = analysis_service.latest(s, s.get(Run, uuid.UUID(env["run"]["id"])), succeeded=True)
        return list(s.scalars(select(Candidate).where(Candidate.analysis_id == a.id).order_by(Candidate.code)))


def by_subjects(rows, subjects):
    return next(c for c in rows if c.subjects == subjects)


def set_objectives(env, objectives):
    with env["sf"]() as s:
        s.get(Run, uuid.UUID(env["run"]["id"])).objectives = objectives
        s.commit()


def test_assessment_on_sample_evidence(env):
    set_objectives(env, ["REDUCE_DUPLICATES", "BSS_SUNSET"])
    started_run(env)
    run_latest(env)
    rows = candidates(env)
    dup = by_subjects(rows, ["PRD-V1", "PRD-V2"])
    assert dup.similarity == 850 and dup.opportunity_score is not None and 0 <= dup.opportunity_score <= 100
    assert dup.dimensions["TECHNICAL"]["status"] == "ASSESSED" and dup.dimensions["TECHNICAL"]["score"] < 80
    rec = dup.recommendation
    assert (rec["action"], rec["targetProductId"], rec["sourceProductId"]) == ("MIGRATE", "PRD-V2", "PRD-V1")
    assert "STANDARDISE_OR_MIGRATE_BEFORE_CONSOLIDATE" in rec["reasonCodes"]  # rule 4
    assert rec["conditions"][0] == "PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL"
    assert dup.impact["activeSubscriptions"] == 86214 and dup.impact["billingSystems"] == 2
    assert dup.impact["summary"] == "£480k / 86k subs" and dup.impact["falloutMultiple"] == 2.3
    assert dup.candidate_status == "REVIEW" and dup.assessment_hash.startswith("sha256:")
    legacy3g = by_subjects(rows, ["PRD-1007"])
    assert "ACTIVE_FOOTPRINT_MIGRATE_NOT_RETIRE" in legacy3g.recommendation["reasonCodes"]  # rule 3
    assert legacy3g.recommendation["action"] != "RETIRE"
    assert all(c.effective_priority in ("LOW", "MEDIUM", "HIGH", "CRITICAL", None) for c in rows)

    ov = env["client"].get(f"{R(env)}/overview", headers=env["pm"]).json()
    m = ov["metrics"]
    assert (m["sourceProducts"], m["candidates"]) == (5, 6)
    assert m["highPriority"] == sum(1 for c in rows if c.effective_priority in ("HIGH", "CRITICAL"))
    with env["sf"]() as s:
        blocked = s.scalar(select(Blocker).where(Blocker.run_id == uuid.UUID(env["run"]["id"])).limit(1))
    assert m["blocked"] == (0 if blocked is None else m["blocked"])
    assert ov["detectorSummary"] == {"rawFindings": 6, "duplicate": 1, "overlap": 3, "legacy": 2, "complexity": 0,
                                     "operationalBurden": 0}
    assert [s["status"] for s in ov["stages"]][:5] == ["COMPLETE"] * 5 and ov["projectionVersion"] >= 1
    assert ov["snapshot"]["code"] == "SNAP-001" and ov["state"] == "REVIEW"


def test_below_review_score_gives_no_recommendation_but_keeps_reasons(env):
    started_run(env)  # objective BSS_SUNSET only: duplicates are weakly aligned
    run_latest(env)
    dup = by_subjects(candidates(env), ["PRD-V1", "PRD-V2"])
    assert dup.score_inputs["strategicAlignment"]["value"] == 400
    assert dup.opportunity_score == 59 and dup.guardrail == "BELOW_REVIEW"
    rec = dup.recommendation
    assert rec["action"] is None and rec["text"] is None and rec["conditions"] == []
    assert {"STANDARDISE_OR_MIGRATE_BEFORE_CONSOLIDATE", "BELOW_REVIEW_THRESHOLD"} <= set(rec["reasonCodes"])
    assert (rec["targetProductId"], rec["sourceProductId"]) == ("PRD-V2", "PRD-V1")


def test_gap_branch_evidence_blocker_and_overview(env):
    files = {k: v for k, v in FILES.items() if k != "PROVISIONING"}
    started_run(env, files)
    run_latest(env)
    dup = by_subjects(candidates(env), ["PRD-V1", "PRD-V2"])
    assert dup.dimensions["TECHNICAL"]["status"] == "NOT_ASSESSED" and dup.dimensions["TECHNICAL"]["score"] is None
    assert dup.recommendation["action"] == "CONSOLIDATE" and dup.recommendation["preliminary"] is True
    assert dup.guardrail == "NOT_ASSESSED" and dup.candidate_status == "EVIDENCE_GAP"

    ov = env["client"].get(f"{R(env)}/overview", headers=env["pm"]).json()
    assert ov["metrics"]["blocked"] >= 1 and ov["attentionTotal"] == ov["metrics"]["blocked"]
    row = next(a for a in ov["attention"] if a["candidate_code"] == dup.code)
    assert row["candidate_id"] == str(dup.id) and row["domain"] == "PROVISIONING" and row["severity"] in (
        "HIGH", "CRITICAL")
    assert "Provisioning" in row["message"] and row["code"].startswith("BLK-")
    severities = [a["severity"] for a in ov["attention"]]
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3}
    assert severities == sorted(severities, key=order.get)

    # reassessment on the same snapshot keeps the same blocker (no duplicates)
    with env["sf"]() as s:
        run = s.get(Run, uuid.UUID(env["run"]["id"]))
        run.state = "ANALYSING"
        analysis_service.request_analysis(s, env["platform"], run, "test", reason="REASSESSMENT")
    run_latest(env)
    again = env["client"].get(f"{R(env)}/overview", headers=env["pm"]).json()
    assert again["metrics"]["blocked"] == ov["metrics"]["blocked"]
    assert {a["code"] for a in again["attention"]} == {a["code"] for a in ov["attention"]}
    assert again["projectionVersion"] > ov["projectionVersion"]

    # a blocker that is no longer raised for an assessed candidate is resolved, not deleted
    with env["sf"]() as s:
        run = s.get(Run, uuid.UUID(env["run"]["id"]))
        a = analysis_service.latest(s, run, succeeded=True)
        snap = s.get(__import__("app.db.models", fromlist=["EvidenceSnapshot"]).EvidenceSnapshot, a.snapshot_id)
        counts = assessment_service.sync_blockers(s, run, a, snap, [], {dup.code})
        s.commit()
        assert counts["resolved"] >= 1
        b = s.scalar(select(Blocker).where(Blocker.run_id == run.id, Blocker.candidate_code == dup.code))
        assert b.state == "RESOLVED" and b.resolution.startswith("No longer applies on SNAP-")
    after = env["client"].get(f"{R(env)}/overview", headers=env["pm"]).json()
    assert after["metrics"]["blocked"] < ov["metrics"]["blocked"]


def test_overview_before_analysis_and_tenancy(env, login):
    ov = env["client"].get(f"{R(env)}/overview", headers=env["viewer"]).json()
    assert ov["metrics"] == {"sourceProducts": None, "candidates": None, "highPriority": None, "blocked": None}
    assert ov["analysis"] is None and [s["status"] for s in ov["stages"]] == ["NOT_STARTED"] * 7
    other = login("portfolio_manager", "t-other-overview")
    assert env["client"].get(f"{R(env)}/overview", headers=other).status_code == 404
