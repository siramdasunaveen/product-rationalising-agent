"""Decision readiness (BUILD_PLAN step 12, Candidate Review). Pure and deterministic; evaluated server-side.

The seven pre-decision gates of the guide, for one candidate and one selected action:
actionPermitted, targetEligibility, minimumEvidenceCoverage, technicalEquivalenceGate, criticalConflicts,
freshness, preDecisionPolicy. continueToDecisionEnabled = every gate PASS, or the selected action is INVESTIGATE.
The inputs are the candidate's persisted gate facts (computed at assessment from the pinned snapshot) plus the live
active blockers, so a stale client can never enable Continue (the API also re-checks snapshot and version).
"""

from typing import Any

ACTIONS = ("RETAIN", "SIMPLIFY", "CONSOLIDATE", "STANDARDISE", "MIGRATE", "RETIRE", "INVESTIGATE")
TARGET_ACTIONS = {"CONSOLIDATE", "MIGRATE", "STANDARDISE"}
# Actions a candidate type may lead to (CLAUDE.md rules 3-5 are added as gate reasons below).
ALLOWED_BY_TYPE: dict[str, tuple[str, ...]] = {
    "DUPLICATE": ("CONSOLIDATE", "STANDARDISE", "MIGRATE", "RETAIN", "INVESTIGATE"),
    "OVERLAP": ("SIMPLIFY", "CONSOLIDATE", "RETAIN", "INVESTIGATE"),
    "LEGACY": ("MIGRATE", "RETIRE", "RETAIN", "INVESTIGATE"),
    "COMPLEXITY": ("SIMPLIFY", "STANDARDISE", "RETAIN", "INVESTIGATE"),
    "OPERATIONAL_BURDEN": ("SIMPLIFY", "MIGRATE", "RETAIN", "INVESTIGATE"),
}
GATES = ("actionPermitted", "targetEligibility", "minimumEvidenceCoverage", "technicalEquivalenceGate",
         "criticalConflicts", "freshness", "preDecisionPolicy")


def allowed_actions(primary: str, supporting: list[str]) -> list[str]:
    out: list[str] = []
    for t in [primary, *supporting]:
        for a in ALLOWED_BY_TYPE.get(t, ("RETAIN", "INVESTIGATE")):
            if a not in out:
                out.append(a)
    return [a for a in ACTIONS if a in out]


def tech_gate_applies(gate: str, action: str) -> bool:
    if action in ("INVESTIGATE", "RETAIN"):
        return False
    return {"REQUIRED_FOR_RECOMMENDATION": True, "REQUIRED_FOR_CONSOLIDATE": action == "CONSOLIDATE",
            "REQUIRED_FOR_MIGRATE": action == "MIGRATE"}.get(gate, False)


def _gate(key: str, ok: bool | None, reason: str) -> dict[str, str]:
    return {"key": key, "status": "PASS" if ok else ("NOT_ASSESSED" if ok is None else "FAIL"), "reason": reason}


def evaluate(action: str, target: str | None, primary: str, supporting: list[str], facts: dict[str, Any],
             settings: dict[str, Any], dispositions: dict[str, list[str]],
             active_blockers: list[dict[str, Any]], subjects: list[str]) -> dict[str, Any]:
    """Gate results for ``action`` (and ``target`` where the action needs one)."""
    gates: list[dict[str, str]] = []
    dims = facts.get("dimensions", {})
    footprint = facts.get("footprint", {})
    eligible = facts.get("eligible", {})
    coverage = {k: set(v) for k, v in facts.get("coverage", {}).items()}
    source = facts.get("sourceProductId") or subjects[0]

    # actionPermitted: candidate type, plus rules 3 and 4
    permitted = action in allowed_actions(primary, supporting)
    reason = f"{action.title()} is allowed for a {primary.lower().replace('_', ' ')} candidate" if permitted else \
        f"{action.title()} is not an option for a {primary.lower().replace('_', ' ')} candidate"
    if permitted and action == "RETIRE":
        fp = footprint.get(source)
        if fp is None:
            permitted, reason = False, "Retire needs a known footprint: active customers/services are unknown"
        elif fp:
            permitted, reason = False, "Active customers or services remain: migrate first, never retire (rule 3)"
    if permitted and action == "CONSOLIDATE":
        tech = dims.get("TECHNICAL", {})
        if tech.get("status") == "ASSESSED" and (tech.get("score") or 0) < facts.get("techPass", 80):
            permitted, reason = False, "Technically different: standardise or migrate before consolidating (rule 4)"
    gates.append(_gate("actionPermitted", permitted, reason))

    # targetEligibility
    if action in TARGET_ACTIONS:
        if not target:
            gates.append(_gate("targetEligibility", False, "Choose a target product"))
        elif target not in eligible:
            gates.append(_gate("targetEligibility", False, f"{target} is not a product of this snapshot"))
        else:
            gates.append(_gate("targetEligibility", bool(eligible[target]),
                               f"{target} is active and eligible" if eligible[target]
                               else f"{target} is not active (end of sale or withdrawn)"))
    else:
        gates.append(_gate("targetEligibility", True, "No target needed"))

    # minimumEvidenceCoverage: commercial, plus technical where the technical gate applies
    minimum = settings.get("minimumEvidenceCoverage", 80)
    needed = ["COMMERCIAL"] + (["TECHNICAL"] if tech_gate_applies(settings.get("technicalEquivalenceGate", ""),
                                                                      action) else [])
    low = [f"{d.title()} {dims.get(d, {}).get('coverage', 0)}%" for d in needed
           if dims.get(d, {}).get("coverage", 0) < minimum]
    gates.append(_gate("minimumEvidenceCoverage", not low, f"Below {minimum}%: {', '.join(low)}" if low
                       else f"Coverage meets {minimum}% for {', '.join(d.lower() for d in needed)}"))

    # technicalEquivalenceGate
    if tech_gate_applies(settings.get("technicalEquivalenceGate", ""), action):
        tech = dims.get("TECHNICAL", {})
        if tech.get("status") in ("NOT_ASSESSED", "BLOCKED"):
            gates.append(_gate("technicalEquivalenceGate", None,
                               f"Technical equivalence {tech.get('status', 'NOT_ASSESSED').lower().replace('_', ' ')}:"
                               " provide provisioning and technical catalog evidence"))
        else:
            ok = (tech.get("score") or 0) >= facts.get("techPass", 80)
            gates.append(_gate("technicalEquivalenceGate", ok, f"Technical equivalence {tech.get('score')} "
                               f"{'≥' if ok else '<'} {facts.get('techPass', 80)}"))
    else:
        gates.append(_gate("technicalEquivalenceGate", True, "Not required for this action"))

    # criticalConflicts
    conflicts = [b for b in active_blockers if b["category"] == "VALIDATION" and b["consequence"] == "BLOCKED"]
    gates.append(_gate("criticalConflicts", not conflicts, "Unresolved material conflict" if conflicts
                       else "No unresolved material conflict"))

    # freshness: mandatory domains of the action must not be stale (unless warnings are allowed)
    mandatory = set(dispositions.get(action, []))
    stale = sorted(mandatory & set(facts.get("staleDomains", [])))
    allow = settings.get("staleEvidenceTreatment") == "ALLOW_WITH_WARNING"
    gates.append(_gate("freshness", not stale or allow, f"Stale: {', '.join(stale)}" if stale and not allow
                       else ("Stale evidence allowed with a warning" if stale else "Evidence is fresh")))

    # preDecisionPolicy: rule 6 (disposition domains AVAILABLE for the candidate's products) + blocking dependencies
    missing = sorted({d for s in subjects for d in mandatory if d not in coverage.get(s, set())})
    blocking = [b for b in active_blockers if b["consequence"] == "BLOCKED" and b["category"] in (
        "DEPENDENCY", "POLICY", "WORKFLOW")]
    if missing:
        gates.append(_gate("preDecisionPolicy", False, f"{action.title()} needs evidence for: {', '.join(missing)}"))
    elif blocking:
        gates.append(_gate("preDecisionPolicy", False, "; ".join(b.get("message", b["category"]) for b in blocking)))
    else:
        gates.append(_gate("preDecisionPolicy", True, "No policy rule needs more evidence"))

    status = "PASS" if all(g["status"] == "PASS" for g in gates) else "BLOCKED"
    return {"action": action, "target": target, "status": status, "gates": gates,
            "continueToDecisionEnabled": status == "PASS" or action == "INVESTIGATE"}
