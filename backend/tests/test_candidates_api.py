"""Step 12 end to end: Candidate Explorer (views, filters, counts), Candidate Review (metrics, evidence,
alternatives, policies, readiness), stale-context fail-closed, discussion and the AI explanation fallback."""

from tests.test_analysis_api import FILES, run_latest, started_run
from tests.test_assessment_api import by_subjects, candidates, set_objectives
from tests.test_reconciliation_api import R, env  # noqa: F401 - env is a fixture

API = "/api/rationalisation"


def analysed(env, files=None):
    set_objectives(env, ["REDUCE_DUPLICATES", "BSS_SUNSET"])
    started_run(env, files)
    run_latest(env)
    return by_subjects(candidates(env), ["PRD-V1", "PRD-V2"])


def test_explorer_views_filters_and_counts(env):
    c = env["client"]
    dup = analysed(env)
    base = f"{R(env)}/candidates"
    all_ = c.get(base, headers=env["pm"]).json()
    assert all_["analysed"] and all_["inventoryTotal"] == 6 and all_["snapshot"]["code"] == "SNAP-001"
    assert all_["effectiveFilters"] == {"includeHidden": False}
    assert all_["filteredCount"] == len(all_["items"]) <= all_["inventoryTotal"]
    row = next(i for i in all_["items"] if i["candidateId"] == dup.code)
    assert row["products"] and row["score"] == dup.opportunity_score and row["recommendation"] == "MIGRATE"
    assert row["primaryTypeLabel"] == "Near duplicate" and row["similarity"] == 850
    # severity ordering: priorities never ascend down the page
    order = {"CRITICAL": 0, "HIGH": 1, "MEDIUM": 2, "LOW": 3, None: 4}
    pr = [order[i["effectivePriority"]] for i in all_["items"]]
    assert pr == sorted(pr)

    hidden = c.get(base, headers=env["pm"], params={"includeHidden": "true"}).json()
    assert hidden["filteredCount"] >= all_["filteredCount"] and hidden["effectiveFilters"]["includeHidden"] is True

    nd = c.get(base, headers=env["pm"], params={"viewId": "VIEW-NEAR-DUPLICATES"}).json()
    assert nd["savedView"]["id"] == "VIEW-NEAR-DUPLICATES" and nd["effectiveFilters"]["types"] == ["DUPLICATE"]
    assert [i["candidateId"] for i in nd["items"]] == [dup.code]

    q = c.get(base, headers=env["pm"], params={"query": dup.code.lower(), "sort": "score:ASC"}).json()
    assert q["filteredCount"] == 1 and q["sort"] == [{"field": "score", "direction": "ASC"}]

    bad = c.get(base, headers=env["pm"], params={"priority": "URGENT"})
    assert bad.status_code == 422 and bad.json()["detail"]["code"] == "validation_failed"
    assert c.get(base, headers=env["pm"], params={"viewId": "VIEW-NOPE"}).status_code == 422


def test_personal_saved_views_are_configuration_only(env, login):
    c = env["client"]
    views = c.get(f"{API}/candidate-views", headers=env["pm"]).json()
    assert [v["scope"] for v in views] == ["TEAM"] * 9
    r = c.post(f"{API}/candidate-views", headers=env["pm"],
               json={"name": "My blocked", "filters": {"status": ["BLOCKED"], "minScore": 60},
                     "visibleColumns": ["id", "score"]})
    assert r.status_code == 201, r.text
    mine = r.json()
    assert mine["scope"] == "PERSONAL" and mine["visibleColumns"] == ["id", "score"] and mine["sort"]
    assert c.post(f"{API}/candidate-views", headers=env["pm"],
                  json={"name": "My blocked", "filters": {}}).status_code == 422  # duplicate name
    assert c.post(f"{API}/candidate-views", headers=env["pm"],
                  json={"name": "x", "filters": {"priority": ["NOPE"]}}).status_code == 422
    assert len(c.get(f"{API}/candidate-views", headers=env["pm"]).json()) == 10
    other = login("product_owner", env["tenant"])
    assert len(c.get(f"{API}/candidate-views", headers=other).json()) == 9  # personal, not shared
    analysed(env)
    used = c.get(f"{R(env)}/candidates", headers=env["pm"], params={"viewId": mine["id"]}).json()
    assert used["effectiveFilters"] == {"status": ["BLOCKED"], "minScore": 60, "includeHidden": False}
    assert c.delete(f"{API}/candidate-views/{mine['id']}", headers=other).status_code == 404
    assert c.delete(f"{API}/candidate-views/{mine['id']}", headers=env["pm"]).status_code == 204
    assert c.get(f"{R(env)}/candidates", headers=env["pm"], params={"viewId": mine["id"]}).status_code == 422


def test_candidate_review_happy_path(env):
    c = env["client"]
    dup = analysed(env)
    url = f"{R(env)}/candidates/{dup.code}"
    d = c.get(url, headers=env["pm"]).json()
    assert d["candidateId"] == dup.code and d["title"].startswith(dup.code + " · ")
    assert d["metrics"]["similarity"] == 0.85 and d["metrics"]["display"]["similarity"] == "0.85"
    assert d["metrics"]["display"]["subscribersExact"] == "86,214 active subscriptions"
    assert d["metrics"]["display"]["opportunityScore"] == f"{dup.opportunity_score}/100"
    assert d["recommendation"]["action"] == "MIGRATE" and d["recommendation"]["targetName"]
    assert d["recommendation"]["aiConfidence"] is None and d["recommendation"]["explanation"] is None
    keys = [r["key"] for r in d["evidence"]]
    assert keys == ["productEquivalence", "customerImpact", "operationalImpact", "dependency", "technical",
                    "lifecycle"]
    for r in d["evidence"]:
        assert r["snapshotId"] == "SNAP-001" and r["freshness"] in ("FRESH", "STALE", "UNKNOWN")
        assert (r["evidenceIds"] != []) == (r["validation"] != "UNKNOWN")
    assert "85% similarity" in d["evidence"][0]["value"]
    alts = {a["action"]: a for a in d["alternatives"]}
    assert alts["MIGRATE"]["recommended"] and alts["MIGRATE"]["feasibilityStatus"] in ("FEASIBLE", "BLOCKED")
    assert "rule 4" in " ".join(alts["CONSOLIDATE"]["blockingReasons"])
    assert alts["RETAIN"]["feasibilityStatus"] == "FEASIBLE"
    assert {p["key"] for p in d["policies"]} >= {"thresholds", "technicalEquivalenceGate", "humanReview",
                                                "approvalRoute"}
    assert d["decisionReadiness"]["action"] == "MIGRATE" and len(d["decisionReadiness"]["gates"]) == 7
    assert d["etag"].startswith('W/"') and d["analysisId"]

    # same candidate by id, and by the id of a superseded version resolves to the current code
    assert c.get(f"{R(env)}/candidates/{d['id']}", headers=env["pm"]).json()["candidateId"] == dup.code
    assert c.get(f"{R(env)}/candidates/CAND-9999", headers=env["pm"]).status_code == 404

    # server-side readiness: matches what Review shows; stale analysis id fails closed
    ok = c.post(f"{url}/decision-readiness", headers=env["pm"],
                json={"action": "migrate", "analysisId": d["analysisId"]})
    assert ok.status_code == 200 and ok.json()["status"] == d["decisionReadiness"]["status"]
    stale = c.post(f"{url}/decision-readiness", headers=env["pm"],
                   json={"action": "MIGRATE", "analysisId": "00000000-0000-0000-0000-000000000000"})
    assert stale.status_code == 409 and stale.json()["detail"]["code"] == "stale"
    inv = c.post(f"{url}/decision-readiness", headers=env["pm"], json={"action": "INVESTIGATE"}).json()
    assert inv["continueToDecisionEnabled"] is True
    assert c.post(f"{url}/decision-readiness", headers=env["pm"], json={"action": "DELETE"}).status_code == 422


def test_gap_branch_readiness_is_blocked_not_zero(env):
    c = env["client"]
    dup = analysed(env, {k: v for k, v in FILES.items() if k != "PROVISIONING"})
    d = c.get(f"{R(env)}/candidates/{dup.code}", headers=env["pm"]).json()
    assert d["status"] == "EVIDENCE_GAP" and d["recommendation"]["preliminary"] is True
    tech = next(r for r in d["evidence"] if r["key"] == "technical")
    assert tech["assessed"] is False and tech["value"] == "Not assessed"
    rd = d["decisionReadiness"]
    assert rd["status"] == "BLOCKED" and rd["continueToDecisionEnabled"] is False
    assert {g["status"] for g in rd["gates"]} & {"FAIL", "NOT_ASSESSED"}
    assert d["blockers"] and d["blockers"][0]["code"].startswith("BLK-")
    gaps = c.get(f"{R(env)}/candidates", headers=env["pm"], params={"viewId": "VIEW-EVIDENCE-GAPS"}).json()
    assert dup.code in [i["candidateId"] for i in gaps["items"]]


def test_discussion_and_explanation_fallback(env):
    c = env["client"]
    dup = analysed(env)
    url = f"{R(env)}/candidates/{dup.code}"
    assert c.get(f"{url}/comments", headers=env["pm"]).json() == []
    r = c.post(f"{url}/comments", headers=env["pm"], json={"body": "  Check the ‮price book\u0007 first "})
    assert r.status_code == 201 and r.json()["body"] == "Check the price book first"
    assert c.post(f"{url}/comments", headers=env["pm"], json={"body": "   "}).status_code == 422
    assert len(c.get(f"{url}/comments", headers=env["pm"]).json()) == 1

    ex = c.post(f"{url}/explanation", headers=env["pm"])
    assert ex.status_code == 201, ex.text
    e = ex.json()
    assert e["source"] == "fallback" and e["aiConfidence"] is None and e["belowThreshold"] is False
    assert e["sentences"] and all(s["evidenceIds"] for s in e["sentences"])
    d = c.get(url, headers=env["pm"]).json()
    assert d["recommendation"]["explanation"]["source"] == "fallback"
    assert d["recommendation"]["aiConfidence"] is None  # confidence only with an accepted LLM explanation


def test_before_analysis_and_tenancy(env, login):
    c = env["client"]
    empty = c.get(f"{R(env)}/candidates", headers=env["pm"]).json()
    assert empty["analysed"] is False and empty["inventoryTotal"] == 0 and empty["items"] == []
    assert c.get(f"{R(env)}/candidates/CAND-0001", headers=env["pm"]).status_code == 404
    other = login("portfolio_manager", "t-other-cand")
    assert c.get(f"{R(env)}/candidates", headers=other).status_code == 404
