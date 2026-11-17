"""Evidence gaps and requests (BUILD_PLAN step 13, screens 13-15). Pure and deterministic.

A gap is an active EVIDENCE blocker of a candidate (a domain its emerging disposition needs is missing or stale for
one of its products). A request is the logical evidence contract for that gap: what is needed (metric, grouping,
filters, return, freshness, privacy), never a vendor query. The user chooses only owner, due date and fulfilment
channel; the contract comes from the policy. Lifecycle: DRAFT -> OPEN -> IN_PROGRESS -> RECEIVED -> ACCEPTED |
REJECTED, and SUPERSEDED when fresher compatible evidence resolved the gap. One evidence return (an approved child
snapshot) can satisfy every request whose domain it refreshes.
"""

from typing import Any

STATUSES = ("DRAFT", "OPEN", "IN_PROGRESS", "RECEIVED", "ACCEPTED", "REJECTED", "SUPERSEDED")
ACTIVE = frozenset({"DRAFT", "OPEN", "IN_PROGRESS", "RECEIVED"})
CLOSED = frozenset({"ACCEPTED", "REJECTED", "SUPERSEDED"})
TRANSITIONS: dict[str, frozenset[str]] = {
    "DRAFT": frozenset({"OPEN", "SUPERSEDED"}),
    "OPEN": frozenset({"OPEN", "IN_PROGRESS", "RECEIVED", "SUPERSEDED"}),
    "IN_PROGRESS": frozenset({"OPEN", "RECEIVED", "SUPERSEDED"}),
    "RECEIVED": frozenset({"ACCEPTED", "REJECTED", "SUPERSEDED"}),
    "REJECTED": frozenset({"RECEIVED", "OPEN", "SUPERSEDED"}),
    "ACCEPTED": frozenset(),
    "SUPERSEDED": frozenset(),
}
FULFILMENT_METHODS = ("SECURE_QUERY", "API_CONNECTOR", "SCHEDULED_EXTRACT", "EXCEL_AGGREGATE")
FULFILMENT_LABELS = {
    "SECURE_QUERY": ("Secure query", "Inside the customer environment"),
    "API_CONNECTOR": ("API connector", "Source system API (live connectors, build step 15)"),
    "SCHEDULED_EXTRACT": ("Scheduled extract", "Encrypted governed drop"),
    "EXCEL_AGGREGATE": ("Excel aggregate", "Approved template upload through the Evidence Hub"),
}
DEFAULT_FRESHNESS_DAYS = 30
PRIVACY = "No subscriber IDs, names, MSISDNs or raw records; aggregates and catalogue metadata only"

# Logical contract per evidence domain: what the integration layer must return, independent of the source system.
CONTRACTS: dict[str, dict[str, Any]] = {
    "CRM_CATALOG": {"metrics": ["Product identity", "Family", "Commercial status", "Offer"],
                    "grouping": "Product ID", "filters": "Products in the run scope", "returns": "Catalogue metadata"},
    "CPQ": {"metrics": ["Rule count", "Variant count", "Rule type"], "grouping": "Product ID",
            "filters": "Active rules", "returns": "Counts and flags only"},
    "PRICE_BOOK": {"metrics": ["List price", "Currency", "Price book"], "grouping": "Product ID + Price book",
                   "filters": "Active entries", "returns": "Prices and flags only"},
    "LIFECYCLE": {"metrics": ["Lifecycle status", "End of sale", "Sunset date", "Replacement"],
                  "grouping": "Product ID", "filters": "Current lifecycle record", "returns": "Dates and status"},
    "TECHNICAL_CATALOG": {"metrics": ["Service specifications", "Resource specifications", "Decomposition"],
                          "grouping": "Product ID + Service Specification ID", "filters": "Active specifications",
                          "returns": "Identifiers and links only"},
    "PROVISIONING": {"metrics": ["Active instances", "Workflow variants", "Compatibility %", "Legacy dependency"],
                     "grouping": "Product ID + Service Specification ID",
                     "filters": "Active instances; current approved snapshot", "returns": "Counts, percentages and flags only"},
    "INVENTORY": {"metrics": ["Active service instances", "Resource footprint", "Legacy platform flag"],
                  "grouping": "Service Specification ID", "filters": "Active instances",
                  "returns": "Aggregate counts only"},
    "ORDER_BILLING_ASSURANCE": {"metrics": ["Orders", "Fallout orders", "Billing system", "Legacy billing code"],
                                "grouping": "Product ID", "filters": "Last reporting period",
                                "returns": "Aggregate counts and flags only"},
    "CUSTOMER_REVENUE_AGGREGATES": {"metrics": ["Active subscriptions", "MRR", "Protected-price contracts"],
                                    "grouping": "Product ID", "filters": "Active customers at the reporting date",
                                    "returns": "Aggregate counts and amounts only"},
}
# Why the disposition needs the domain (guide p37: dynamic request triggers).
PURPOSE: dict[tuple[str, str], str] = {
    ("CONSOLIDATE", "PROVISIONING"): "Confirm whether both products can share the same target fulfilment.",
    ("CONSOLIDATE", "TECHNICAL_CATALOG"): "Confirm commercial and technical equivalence of the service mapping.",
    ("STANDARDISE", "PROVISIONING"): "Confirm the common target model readiness of the provisioning variants.",
    ("STANDARDISE", "TECHNICAL_CATALOG"): "Confirm the service specifications and attribute variants to standardise.",
    ("MIGRATE", "INVENTORY"): "Establish the active technical footprint that must be migrated.",
    ("MIGRATE", "PROVISIONING"): "Confirm target compatibility and exception rates for the migration.",
    ("MIGRATE", "CUSTOMER_REVENUE_AGGREGATES"): "Size the active base and revenue to migrate.",
    ("RETIRE", "INVENTORY"): "Prove there is no active technical footprint before retirement.",
    ("RETIRE", "LIFECYCLE"): "Confirm the lifecycle dates that permit retirement.",
    ("RETIRE", "CUSTOMER_REVENUE_AGGREGATES"): "Confirm installed base, contracts and revenue are safe to retire.",
    ("SIMPLIFY", "CPQ"): "Measure the configuration variants and rule counts to simplify.",
}
UNLOCKS: dict[str, str] = {
    "SIMPLIFY": "Complexity and simplification benefit", "CONSOLIDATE": "Commercial and technical equivalence",
    "STANDARDISE": "Common target model readiness", "MIGRATE": "Migration readiness",
    "RETIRE": "Retirement safety and approval", "RETAIN": "Continued-value assessment",
    "INVESTIGATE": "Next valid disposition",
}


def can_transition(current: str, target: str) -> bool:
    return target in TRANSITIONS.get(current, frozenset())


def contract(domain: str, action: str, products: list[str], *, stale: bool = False,
             freshness_days: int | None = None) -> dict[str, Any]:
    """The logical evidence contract of a gap. Users never edit it."""
    base = CONTRACTS.get(domain, {"metrics": ["Evidence"], "grouping": "Product ID", "filters": "Run scope",
                                  "returns": "Aggregates only"})
    purpose = PURPOSE.get((action, domain)) or (
        f"Refresh {domain.replace('_', ' ').title()} evidence so the {action.lower()} assessment can proceed."
        if stale else f"Provide the {domain.replace('_', ' ').title()} evidence the {action.lower()} assessment needs.")
    return {"version": "1.0", "purpose": purpose, "domain": domain, "metrics": list(base["metrics"]),
            "grouping": base["grouping"], "filters": base["filters"], "returns": base["returns"],
            "freshnessDays": freshness_days or DEFAULT_FRESHNESS_DAYS, "privacy": PRIVACY,
            "products": sorted(products), "unlocks": UNLOCKS.get(action, "Assessment")}


def decision_impact(action: str | None, domain: str, stale: bool) -> str:
    if stale:
        return f"{domain.replace('_', ' ').title()} evidence older than its freshness limit"
    if domain in ("PROVISIONING", "TECHNICAL_CATALOG") and action in ("CONSOLIDATE", "STANDARDISE"):
        return "Technical equivalence not assessed"
    if action in ("MIGRATE", "RETIRE"):
        return f"{action.title()} approval blocked"
    return f"{(action or 'Assessment').title()} readiness blocked"


def compatible(request: dict[str, Any], snapshot_inputs: list[dict[str, Any]]) -> bool:
    """A snapshot fulfils a request when it carries an execution of the request's domain the request has not seen
    (the baseline executions are those of the snapshot the gap was found on, plus any rejected return)."""
    seen = set(request.get("baselineExecutions") or [])
    return any(i.get("domain") == request["domain"] and i.get("execution_id") not in seen for i in snapshot_inputs)


def queue_metrics(items: list[dict[str, Any]]) -> dict[str, Any]:
    active = [r for r in items if r["status"] in ACTIVE]
    return {
        "open": len(active), "openCandidates": len({r["candidateCode"] for r in active}),
        "blocking": sum(1 for r in active if r.get("consequence") == "BLOCKED"),
        "awaiting": sum(1 for r in active if r["status"] in ("OPEN", "IN_PROGRESS")),
        "ready": sum(1 for r in active if r["status"] == "RECEIVED"),
        "reassessing": sum(1 for r in items if r["status"] == "ACCEPTED" and r.get("reassessmentStatus") == "QUEUED"),
    }
