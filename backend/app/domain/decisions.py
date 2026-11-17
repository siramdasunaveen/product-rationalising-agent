"""Decisions and approvals (BUILD_PLAN step 14, screens 09 and 10). Pure and deterministic.

A Decision is human-owned: disposition, target, effective date, owner and rationale. Every save creates a new
immutable version (SHA-256 content hash over the canonical content, the snapshot, the manifest and the
recommendation it was taken on). Submission is gated by the server-side decision readiness (rules 5 and 6) and the
execution-readiness gate. The policy pack routes the submitted version to approval scopes; approvers act on the
exact version and hash; Reject, Return and Hold need a comment; self-approval can be disabled; the decision is
APPROVED when every required scope approved and no policy check is BLOCKED. Approval changes no product.
"""

import hashlib
from datetime import date, timedelta
from typing import Any

from app.domain.artifacts import canonical_json
from app.domain.common import FieldError

DISPOSITIONS = ("RETAIN", "SIMPLIFY", "CONSOLIDATE", "STANDARDISE", "MIGRATE", "RETIRE", "INVESTIGATE")
TARGET_ACTIONS = frozenset({"CONSOLIDATE", "MIGRATE", "STANDARDISE"})
RATIONALE_MIN, RATIONALE_MAX = 50, 4000
COMMENT_MAX = 2000
STATES = ("DRAFT", "SUBMITTED", "ON_HOLD", "RETURNED", "APPROVED", "REJECTED", "SUPERSEDED")
OPEN_STATES = frozenset({"DRAFT", "SUBMITTED", "ON_HOLD", "RETURNED"})
ACTIONS = ("APPROVE", "RETURN", "HOLD", "REJECT")
COMMENT_REQUIRED = frozenset({"RETURN", "HOLD", "REJECT"})
COMMERCIAL_TREATMENT = {
    "PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL": "Grandfather price until renewal",
    "MIGRATE_LEGACY_BILLING_DEPENDENCY": "Migrate the legacy billing dependency before cut-over",
    "MIGRATE_ACTIVE_BASE": "Migrate the active base in cohorts",
}
NOTICE_DAYS = 30


def content_hash(content: dict[str, Any]) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(content)).hexdigest()


def is_business_day(d: date) -> bool:
    return d.weekday() < 5


def validate(form: dict[str, Any], *, eligible_targets: dict[str, bool], today: date,
             allowed_actions: list[str]) -> list[FieldError]:
    """Field validation of the decision form; the server validates the target against the snapshot."""
    errors: list[FieldError] = []
    action = form.get("disposition")
    if action not in DISPOSITIONS:
        errors.append(FieldError("disposition", f"Choose one of {', '.join(DISPOSITIONS)}"))
    elif action not in allowed_actions:
        errors.append(FieldError("disposition", f"{action.title()} is not an option for this candidate"))
    target = form.get("targetProductId")
    if action in TARGET_ACTIONS:
        if not target:
            errors.append(FieldError("targetProductId", "This disposition needs a target product"))
        elif target not in eligible_targets:
            errors.append(FieldError("targetProductId", "Choose a product of the candidate's snapshot"))
        elif not eligible_targets[target]:
            errors.append(FieldError("targetProductId", "The target is not active (end of sale or withdrawn)"))
    elif target:
        errors.append(FieldError("targetProductId", "This disposition takes no target"))
    eff = form.get("effectiveDate")
    if not isinstance(eff, date):
        errors.append(FieldError("effectiveDate", "Enter the effective date (YYYY-MM-DD)"))
    elif eff <= today:
        errors.append(FieldError("effectiveDate", "The effective date must be in the future"))
    elif not is_business_day(eff):
        errors.append(FieldError("effectiveDate", "Choose a business day (Monday to Friday)"))
    if not (form.get("ownerId") or "").strip():
        errors.append(FieldError("ownerId", "Name the accountable decision owner"))
    rationale = (form.get("rationale") or "").strip()
    if not RATIONALE_MIN <= len(rationale) <= RATIONALE_MAX:
        errors.append(FieldError("rationale", f"Use {RATIONALE_MIN}-{RATIONALE_MAX} characters"))
    return errors


def submission_blockers(readiness: dict[str, Any] | None, settings: dict[str, Any], dimensions: dict[str, dict],
                        action: str) -> list[str]:
    """Why the decision cannot be submitted (rule 5/6 via the readiness gates, plus the execution gate)."""
    out: list[str] = []
    if action == "INVESTIGATE":
        return out
    if readiness is None:
        out.append("Decision readiness has not been evaluated for this disposition")
    elif not readiness.get("continueToDecisionEnabled"):
        out += [g["reason"] for g in readiness.get("gates", []) if g["status"] != "PASS"]
    if settings.get("executionReadinessGate") == "REQUIRED_FOR_DECISION":
        ex = dimensions.get("EXECUTION", {})
        if ex.get("status") in (None, "NOT_ASSESSED", "BLOCKED"):
            out.append("Execution readiness is not assessed; the policy requires it before a decision")
    return out


def approval_route(policy: dict[str, Any], action: str, impact: dict[str, Any],
                   priority_overrides: list[dict[str, Any]]) -> dict[str, Any]:
    """Required approval scopes: the policy pack route for the disposition, escalated by customer impact."""
    route = next((r for r in policy.get("approvalRoutes", []) if action in r.get("dispositions", [])), None)
    scopes = list(route["scopes"]) if route else ["product"]
    reasons = [route.get("reason", "Policy route")] if route else ["Default product approval"]
    threshold = next((o.get("minSubscriptions") for o in priority_overrides if o.get("kind") == "CUSTOMER_IMPACT"),
                     None)
    subs = impact.get("activeSubscriptions")
    if threshold and subs is not None and subs >= threshold and "commercial" not in scopes:
        scopes.append("commercial")
        reasons.append(f"Customer impact: {subs:,} active subscriptions (>= {threshold:,})")
    if impact.get("legacyBillingCode") and action in ("CONSOLIDATE", "MIGRATE", "RETIRE") and "architecture" not in scopes:
        scopes.append("architecture")
        reasons.append("Legacy billing dependency")
    return {"scopes": scopes, "reasons": reasons, "policyRef": policy.get("_ref")}


def policy_checks(version: dict[str, Any], candidate: dict[str, Any], settings: dict[str, Any],
                  readiness: dict[str, Any] | None, *, stale: bool) -> list[dict[str, Any]]:
    """Policy checks shown on Approval (PASS | WARNING | BLOCKED). Re-evaluated on every read; stale = the
    candidate was reassessed since this version was taken."""
    action = version["disposition"]
    impact = candidate.get("impact") or {}
    dims = candidate.get("dimensions") or {}
    conditions = version.get("conditions") or []
    checks = []
    if stale:
        checks.append({"key": "version", "label": "Decision version", "status": "BLOCKED",
                       "detail": "The candidate was reassessed after this version; a new version is needed"})
    else:
        checks.append({"key": "version", "label": "Decision version", "status": "PASS",
                       "detail": f"Version {version['versionNo']} on {version['snapshotCode']}"})
    protected = impact.get("protectedPriceContracts") or 0
    if "PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL" in conditions or protected:
        ok = bool(version.get("commercialTreatment"))
        checks.append({"key": "commercialTreatment", "label": "Commercial treatment", "status": "PASS" if ok else "BLOCKED",
                       "detail": version.get("commercialTreatment") or "Protected-price contracts need a treatment"})
    else:
        checks.append({"key": "commercialTreatment", "label": "Commercial treatment", "status": "PASS",
                       "detail": "No contracted-price constraint"})
    if protected and action in ("CONSOLIDATE", "MIGRATE", "RETIRE"):
        checks.append({"key": "customerNotice", "label": "Customer notice", "status": "WARNING",
                       "detail": f"{NOTICE_DAYS}-day notice for {protected:,} contracts"})
    else:
        checks.append({"key": "customerNotice", "label": "Customer notice", "status": "PASS",
                       "detail": "No contractual notice period triggered"})
    mrr = impact.get("mrr")
    checks.append({"key": "revenueImpact", "label": "Revenue impact", "status": "PASS",
                   "detail": (f"{mrr['currency']} {mrr['amount']:,} MRR in scope" if mrr else "Revenue not assessed")
                   if action != "RETIRE" or mrr else "Revenue not assessed"})
    tech = dims.get("TECHNICAL", {})
    gate = settings.get("technicalEquivalenceGate", "")
    needs_tech = action in ("CONSOLIDATE", "STANDARDISE", "MIGRATE") and gate != "NOT_REQUIRED"
    if needs_tech and tech.get("status") in (None, "NOT_ASSESSED", "BLOCKED"):
        checks.append({"key": "architecture", "label": "Architecture", "status": "BLOCKED",
                       "detail": "Technical equivalence not assessed"})
    else:
        checks.append({"key": "architecture", "label": "Architecture", "status": "PASS",
                       "detail": f"Technical equivalence {tech.get('score')}" if tech.get("score") is not None
                       else "Not required for this disposition"})
    if settings.get("executionReadinessGate") == "REQUIRED_FOR_APPROVAL" and action != "INVESTIGATE":
        ex = dims.get("EXECUTION", {})
        ok = ex.get("status") not in (None, "NOT_ASSESSED", "BLOCKED")
        checks.append({"key": "executionReadiness", "label": "Execution readiness", "status": "PASS" if ok else "BLOCKED",
                       "detail": f"Execution {ex.get('score')}" if ok else "Execution readiness not assessed"})
    if readiness is not None and action != "INVESTIGATE":
        failed = [g["reason"] for g in readiness.get("gates", []) if g["status"] != "PASS"]
        checks.append({"key": "evidence", "label": "Evidence and policy gates", "status": "BLOCKED" if failed else "PASS",
                       "detail": "; ".join(failed) if failed else "All pre-decision gates pass on the current snapshot"})
    return checks


def overall(checks: list[dict[str, Any]]) -> str:
    statuses = {c["status"] for c in checks}
    return "BLOCKED" if "BLOCKED" in statuses else ("PASS_WITH_WARNING" if "WARNING" in statuses else "PASS")


def validate_action(action: str, comment: str | None, *, scope_pending: bool, has_scope: bool,
                    self_approval: bool, allow_self_approval: bool, blocked: bool,
                    expected_hash: str | None, actual_hash: str) -> list[str]:
    problems = []
    if action not in ACTIONS:
        problems.append(f"Choose one of {', '.join(ACTIONS)}")
    if action in COMMENT_REQUIRED and not (comment or "").strip():
        problems.append(f"{action.title()} needs a comment")
    if comment and len(comment) > COMMENT_MAX:
        problems.append(f"Comments are limited to {COMMENT_MAX} characters")
    if not has_scope:
        problems.append("You hold no approval scope this decision requires")
    elif not scope_pending:
        problems.append("Your scope has already acted on this version")
    if self_approval and not allow_self_approval and action == "APPROVE":
        problems.append("Self-approval is disabled by the policy pack")
    if expected_hash and expected_hash != actual_hash:
        problems.append("The decision version changed; reload before acting")
    if blocked and action == "APPROVE":
        problems.append("A policy check is BLOCKED; the decision cannot be approved")
    return problems


def next_state(current: str, action: str, tasks: list[dict[str, Any]]) -> str:
    """Decision state after an approval action. ``tasks`` are the route's scopes with their state after the action."""
    if action == "REJECT":
        return "REJECTED"
    if action == "RETURN":
        return "RETURNED"
    if action == "HOLD":
        return "ON_HOLD"
    if all(t["state"] == "APPROVED" for t in tasks):
        return "APPROVED"
    return "SUBMITTED"


def material_change(version: dict[str, Any], candidate: dict[str, Any]) -> list[str]:
    """Why a reassessment materially changes a decision taken on an earlier assessment (guide p44)."""
    rec = candidate.get("recommendation") or {}
    reasons = []
    if rec.get("action") != version.get("recommendedAction"):
        reasons.append(f"Recommendation changed from {version.get('recommendedAction') or 'none'} to {rec.get('action') or 'none'}")
    if version["disposition"] in TARGET_ACTIONS and rec.get("targetProductId") not in (None, version.get("targetProductId")):
        reasons.append(f"Suggested target changed to {rec.get('targetProductId')}")
    status = candidate.get("status")
    if status in ("BLOCKED", "EVIDENCE_GAP"):
        reasons.append(f"Candidate is now {status.lower().replace('_', ' ')}")
    rd = candidate.get("readinessForDecision")
    if rd is not None and rd.get("status") != "PASS" and version["disposition"] != "INVESTIGATE":
        reasons.append("Decision readiness no longer passes for the chosen disposition")
    return reasons


def default_effective_date(today: date, lead_days: int = 90) -> date:
    d = today + timedelta(days=lead_days)
    while not is_business_day(d):
        d += timedelta(days=1)
    return d
