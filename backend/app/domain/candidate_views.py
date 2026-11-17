"""Candidate Explorer views and filters (BUILD_PLAN step 12, screen 07). Pure and deterministic.

A saved view is configuration only: filters, sort and visible columns over the same Candidate read model. The
server applies exactly the filters it reports back as ``effectiveFilters`` (the UI draws its filter bar from them),
counts ``inventoryTotal`` (every active candidate of the run) separately from ``filteredCount``, and never derives
Priority from Score.
"""

from dataclasses import dataclass, field
from typing import Any

PRIORITIES = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
STATUSES = ("REVIEW", "BLOCKED", "EVIDENCE_GAP", "DECISION", "APPROVAL", "APPROVED")
TYPES = ("DUPLICATE", "OVERLAP", "LEGACY", "COMPLEXITY", "OPERATIONAL_BURDEN")
ACTIONS = ("RETAIN", "SIMPLIFY", "CONSOLIDATE", "STANDARDISE", "MIGRATE", "RETIRE", "INVESTIGATE")
READINESS = ("PASS", "BLOCKED")
COLUMNS = ("id", "products", "type", "score", "priority", "recommendation", "impact", "status")
SORT_FIELDS = ("priority", "score", "id", "status", "type", "recommendation")
MAX_QUERY = 100
LIST_FILTERS = {"priority": PRIORITIES, "status": STATUSES, "types": TYPES, "recommendation": ACTIONS,
                "decisionReadiness": READINESS}
DEFAULT_SORT = [{"field": "priority", "direction": "DESC"}, {"field": "score", "direction": "DESC"},
                {"field": "id", "direction": "ASC"}]


@dataclass(frozen=True)
class View:
    id: str
    name: str
    filters: dict[str, Any]
    purpose: str
    sort: list[dict[str, str]] = field(default_factory=lambda: list(DEFAULT_SORT))
    columns: tuple[str, ...] = COLUMNS
    scope: str = "TEAM"


# Built-in team views (declared semantics; "type" filters match primary OR supporting types).
BUILTIN_VIEWS: tuple[View, ...] = (
    View("VIEW-ALL", "All candidates", {}, "General portfolio exploration"),
    View("VIEW-HIGH-PRIORITY", "High-priority rationalisation", {"priority": ["HIGH", "CRITICAL"]},
         "Focused prioritisation",
         [{"field": "priority", "direction": "DESC"}, {"field": "score", "direction": "DESC"},
          {"field": "id", "direction": "ASC"}]),
    View("VIEW-BLOCKED", "Blocked candidates", {"status": ["BLOCKED"]}, "Resolve blockers (status = BLOCKED)"),
    View("VIEW-EVIDENCE-GAPS", "Evidence gaps", {"status": ["EVIDENCE_GAP"]}, "Drive evidence collection"),
    View("VIEW-NEAR-DUPLICATES", "Near duplicates", {"types": ["DUPLICATE"]}, "Consolidation review"),
    View("VIEW-LEGACY", "Legacy dependencies", {"types": ["LEGACY"]}, "Migration and sunset planning"),
    View("VIEW-OPERATIONAL-BURDEN", "Operational burden", {"types": ["OPERATIONAL_BURDEN"]},
         "Simplification and operations improvement"),
    View("VIEW-READY-FOR-DECISION", "Ready for decision", {"status": ["REVIEW"], "decisionReadiness": ["PASS"]},
         "Move reviewed candidates to Decision"),
    View("VIEW-IN-APPROVAL", "In approval", {"status": ["APPROVAL"]}, "Track submitted decisions"),
)
VIEWS_BY_ID = {v.id: v for v in BUILTIN_VIEWS}


def validate_filters(filters: dict[str, Any]) -> list[str]:
    """Problems with a filter set (unknown keys or values). Used for saved views and query parameters."""
    problems = []
    for key, value in filters.items():
        if key in LIST_FILTERS:
            if not isinstance(value, list) or not value:
                problems.append(f"{key} must be a non-empty list")
                continue
            bad = [v for v in value if v not in LIST_FILTERS[key]]
            if bad:
                problems.append(f"{key}: unknown value(s) {', '.join(map(str, bad))}")
        elif key == "minScore":
            if not isinstance(value, int) or isinstance(value, bool) or not 0 <= value <= 100:
                problems.append("minScore must be a whole number from 0 to 100")
        elif key == "query":
            if not isinstance(value, str) or not value.strip() or len(value) > MAX_QUERY:
                problems.append(f"query must be 1-{MAX_QUERY} characters")
        elif key == "includeHidden":
            if not isinstance(value, bool):
                problems.append("includeHidden must be true or false")
        else:
            problems.append(f"Unknown filter {key}")
    return problems


def validate_sort(sort: list[dict[str, str]]) -> list[str]:
    problems = []
    for s in sort:
        if s.get("field") not in SORT_FIELDS or s.get("direction") not in ("ASC", "DESC"):
            problems.append(f"Unsupported sort {s}")
    return problems


def effective_filters(view: View | None, explicit: dict[str, Any]) -> dict[str, Any]:
    """View filters, then explicit parameters replace the same keys. Hidden candidates stay out of the default
    (unfiltered) view unless includeHidden is set; that exclusion is reported like any other filter."""
    out: dict[str, Any] = dict(view.filters) if view else {}
    out.update({k: v for k, v in explicit.items() if v not in (None, [], "")})
    if "includeHidden" not in out:
        out["includeHidden"] = False
    return out


def matches(item: dict[str, Any], filters: dict[str, Any]) -> bool:
    if not filters.get("includeHidden") and item.get("hidden"):
        return False
    if "priority" in filters and item.get("effectivePriority") not in filters["priority"]:
        return False
    if "status" in filters and item.get("status") not in filters["status"]:
        return False
    if "types" in filters and not set(filters["types"]) & {item["primaryType"], *item.get("supportingTypes", [])}:
        return False
    if "recommendation" in filters and item.get("recommendation") not in filters["recommendation"]:
        return False
    if "decisionReadiness" in filters and item.get("decisionReadiness") not in filters["decisionReadiness"]:
        return False
    if "minScore" in filters and (item.get("score") is None or item["score"] < filters["minScore"]):
        return False
    if "query" in filters:
        q = filters["query"].strip().lower()
        hay = " ".join([item["candidateId"], *item.get("products", []), *item.get("productIds", [])]).lower()
        if q not in hay:
            return False
    return True


def _key(item: dict[str, Any], f: str) -> Any:
    if f == "priority":
        p = item.get("effectivePriority")
        return PRIORITIES.index(p) if p in PRIORITIES else -1  # severity order, never alphabetical
    if f == "score":
        return item.get("score") if item.get("score") is not None else -1
    if f == "id":
        return item["candidateId"]
    if f == "status":
        return STATUSES.index(item["status"]) if item.get("status") in STATUSES else -1
    if f == "type":
        return item.get("primaryType") or ""
    return item.get("recommendation") or ""


def sort_items(items: list[dict[str, Any]], sort: list[dict[str, str]]) -> list[dict[str, Any]]:
    out = list(items)
    for s in reversed(sort + [{"field": "id", "direction": "ASC"}]):  # stable: last key first, id breaks ties
        out.sort(key=lambda it, f=s["field"]: _key(it, f), reverse=s["direction"] == "DESC")
    return out


def apply(items: list[dict[str, Any]], view: View | None, explicit: dict[str, Any], sort: list[dict] | None,
          page: int, page_size: int) -> dict[str, Any]:
    filters = effective_filters(view, explicit)
    hits = [it for it in items if matches(it, filters)]
    order = sort or (view.sort if view else DEFAULT_SORT)
    ordered = sort_items(hits, order)
    start = (page - 1) * page_size
    return {"inventoryTotal": len(items), "filteredCount": len(hits), "effectiveFilters": filters, "sort": order,
            "items": ordered[start:start + page_size], "page": page, "pageSize": page_size}
