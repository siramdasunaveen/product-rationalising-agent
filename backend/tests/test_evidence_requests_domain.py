"""Step 13 domain: logical evidence contracts, lifecycle transitions, return compatibility, queue metrics."""

from app.domain import evidence_requests as er


def test_contract_is_system_owned_and_aggregate_only():
    c = er.contract("PROVISIONING", "CONSOLIDATE", ["PRD-V2", "PRD-V1"])
    assert c["purpose"] == "Confirm whether both products can share the same target fulfilment."
    assert c["grouping"] == "Product ID + Service Specification ID" and c["freshnessDays"] == 30
    assert c["products"] == ["PRD-V1", "PRD-V2"] and "No subscriber IDs" in c["privacy"]
    assert c["unlocks"] == "Commercial and technical equivalence"
    stale = er.contract("CPQ", "SIMPLIFY", ["PRD-1"], stale=True, freshness_days=45)
    assert stale["freshnessDays"] == 45 and stale["purpose"].startswith("Measure the configuration")
    other = er.contract("INVENTORY", "RETAIN", ["PRD-1"], stale=True)
    assert other["purpose"].startswith("Refresh Inventory evidence")


def test_decision_impact_wording():
    assert er.decision_impact("CONSOLIDATE", "PROVISIONING", False) == "Technical equivalence not assessed"
    assert er.decision_impact("MIGRATE", "INVENTORY", False) == "Migrate approval blocked"
    assert er.decision_impact("SIMPLIFY", "CPQ", True).endswith("older than its freshness limit")


def test_lifecycle_transitions():
    assert er.can_transition("DRAFT", "OPEN") and not er.can_transition("DRAFT", "RECEIVED")
    assert er.can_transition("OPEN", "RECEIVED") and er.can_transition("IN_PROGRESS", "RECEIVED")
    assert er.can_transition("RECEIVED", "ACCEPTED") and er.can_transition("RECEIVED", "REJECTED")
    assert er.can_transition("REJECTED", "RECEIVED") and not er.can_transition("ACCEPTED", "OPEN")
    assert not er.can_transition("SUPERSEDED", "OPEN")


def test_compatible_return_needs_a_new_execution_of_the_domain():
    req = {"domain": "PROVISIONING", "baselineExecutions": ["ex-1"]}
    assert not er.compatible(req, [{"domain": "PROVISIONING", "execution_id": "ex-1"}])
    assert not er.compatible(req, [{"domain": "INVENTORY", "execution_id": "ex-9"}])
    assert er.compatible(req, [{"domain": "PROVISIONING", "execution_id": "ex-2"}])


def test_queue_metrics():
    items = [{"status": "DRAFT", "candidateCode": "CAND-0001", "consequence": "BLOCKED"},
             {"status": "OPEN", "candidateCode": "CAND-0001", "consequence": "BLOCKED"},
             {"status": "IN_PROGRESS", "candidateCode": "CAND-0002", "consequence": "WARNING_ONLY"},
             {"status": "RECEIVED", "candidateCode": "CAND-0003", "consequence": "BLOCKED"},
             {"status": "ACCEPTED", "candidateCode": "CAND-0004", "reassessmentStatus": "QUEUED"},
             {"status": "SUPERSEDED", "candidateCode": "CAND-0005"}]
    assert er.queue_metrics(items) == {"open": 4, "openCandidates": 3, "blocking": 3, "awaiting": 2, "ready": 1,
                                       "reassessing": 1}
