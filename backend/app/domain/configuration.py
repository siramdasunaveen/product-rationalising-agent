"""Scoring & AI Route configuration (BUILD_PLAN step 9, screens 04/05). Pure and deterministic.

Registry content of the four configuration kinds (detector pack, scoring profile, priority policy, policy pack),
the deterministic recommendation of packs for a run (with a trace; ties are presented, never invented),
threshold/treatment precedence (authorised run override -> policy pack -> scoring profile -> Telco default),
and the compatibility check (PASS / WARNING / BLOCKED with INFO / WARNING / BLOCKING issues).

Numbers in registry content are integers (canonical JSON rejects floats): weights are percentages that total
100 and similarity/detector thresholds are per mille (900 = 0.90).
"""

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.domain.common import FieldError
from app.domain.runs import Objective


class Dimension(StrEnum):
    COMMERCIAL = "COMMERCIAL"
    TECHNICAL = "TECHNICAL"
    EXECUTION = "EXECUTION"


class DetectorType(StrEnum):
    DUPLICATE = "DUPLICATE"
    OVERLAP = "OVERLAP"
    LEGACY = "LEGACY"
    COMPLEXITY = "COMPLEXITY"
    OPERATIONAL_BURDEN = "OPERATIONAL_BURDEN"


DETECTOR_FACTORS: dict[DetectorType, tuple[str, ...]] = {
    DetectorType.DUPLICATE: ("service", "cpq", "eligibility", "pricingStructure", "technical", "lifecycle"),
    DetectorType.OVERLAP: ("capability", "segment", "channelMarket", "useCase", "substitution"),
    DetectorType.LEGACY: (),
    DetectorType.COMPLEXITY: ("variant", "rule", "bundleDepth", "attributeEntropy", "manualException"),
    DetectorType.OPERATIONAL_BURDEN: ("fallout", "manualTouch", "incident", "provisioningException", "handling"),
}
SCORE_FACTORS = ("detectorSignal", "commercialValue", "technicalFeasibility", "operationalBenefit",
                 "strategicAlignment")
PRIORITY_FACTORS = ("businessImpact", "exposure", "operationalBurden", "legacyUrgency", "regulatoryRisk",
                    "dependencyCriticality", "strategicAlignment")
BANDS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
OVERRIDE_KINDS = ("REGULATORY", "SUNSET", "DEPENDENCY", "CUSTOMER_IMPACT")
DISPOSITIONS = ("RETAIN", "SIMPLIFY", "CONSOLIDATE", "STANDARDISE", "MIGRATE", "RETIRE", "INVESTIGATE")
APPROVAL_SCOPES = ("commercial", "product", "architecture")

BELOW_REVIEW = ("NO_RECOMMENDATION", "INVESTIGATE", "HIDE_FROM_DEFAULT_VIEW")
MISSING_EVIDENCE = ("NOT_ASSESSED", "REVIEW_REQUIRED", "BLOCK")  # ASSUME_ZERO is prohibited (CLAUDE.md rule 1)
CRITICAL_CONFLICT = ("BLOCK", "REQUIRE_STEWARD_REVIEW")
STALE_EVIDENCE = ("REQUIRE_REFRESH", "ALLOW_WITH_WARNING", "BLOCK")
TECH_GATE = ("NOT_REQUIRED", "REQUIRED_FOR_RECOMMENDATION", "REQUIRED_FOR_CONSOLIDATE", "REQUIRED_FOR_MIGRATE",
             "REQUIRED_FOR_APPROVAL")
EXEC_GATE = ("INFORMATIONAL", "REQUIRED_FOR_DECISION", "REQUIRED_FOR_APPROVAL")

# Telco defaults: the last step of the precedence chain.
TELCO_DEFAULTS: dict[str, Any] = {
    "recommendedThreshold": 80, "reviewThreshold": 60, "belowReviewTreatment": "NO_RECOMMENDATION",
    "minimumEvidenceCoverage": 80, "missingEvidenceTreatment": "NOT_ASSESSED", "criticalConflictTreatment": "BLOCK",
    "staleEvidenceTreatment": "REQUIRE_REFRESH", "technicalEquivalenceGate": "REQUIRED_FOR_CONSOLIDATE",
    "executionReadinessGate": "REQUIRED_FOR_DECISION", "humanReviewRequired": True,
}
SETTING_VALUES: dict[str, Any] = {
    "recommendedThreshold": ("int", 0, 100), "reviewThreshold": ("int", 0, 100),
    "minimumEvidenceCoverage": ("int", 0, 100), "belowReviewTreatment": BELOW_REVIEW,
    "missingEvidenceTreatment": MISSING_EVIDENCE, "criticalConflictTreatment": CRITICAL_CONFLICT,
    "staleEvidenceTreatment": STALE_EVIDENCE, "technicalEquivalenceGate": TECH_GATE,
    "executionReadinessGate": EXEC_GATE, "humanReviewRequired": ("bool",),
}

# ---------------------------------------------------------------------------
# Content schemas
# ---------------------------------------------------------------------------

_CODE = {"type": "string", "pattern": r"^[A-Z][A-Z0-9_]{1,39}$"}
_PCT = {"type": "integer", "minimum": 0, "maximum": 100}
_MILLE = {"type": "integer", "minimum": 0, "maximum": 1000}
APPLICABILITY = {
    "type": "object", "additionalProperties": False,
    "required": ["markets", "productFamilies", "objectives", "dimensions"],
    "properties": {
        "markets": {"type": "array", "items": _CODE, "minItems": 1, "uniqueItems": True},
        "productFamilies": {"type": "array", "items": _CODE, "minItems": 1, "uniqueItems": True},
        "objectives": {"type": "array", "items": {"enum": [o.value for o in Objective]}, "uniqueItems": True},
        "dimensions": {"type": "array", "items": {"enum": [d.value for d in Dimension]}, "uniqueItems": True},
        "canonicalSchema": {"type": "string", "maxLength": 64},
        "priority": {"type": "integer", "minimum": 0, "maximum": 100},
    },
}


def _weights(keys: tuple[str, ...]) -> dict:
    return {"type": "object", "additionalProperties": False, "required": list(keys),
            "properties": {k: _PCT for k in keys}}


DETECTOR_PACK_JSON = {
    "type": "object", "additionalProperties": False,
    "required": ["applicability", "detectors", "primaryTypePrecedence", "normalisation", "grouping"],
    "properties": {
        "description": {"type": "string", "maxLength": 2000},
        "applicability": APPLICABILITY,
        "detectors": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False, "required": ["type", "version", "enabled"],
            "properties": {
                "type": {"enum": [t.value for t in DetectorType]},
                "version": {"type": "string", "pattern": r"^\d+\.\d+$"},
                "enabled": {"type": "boolean"},
                "factors": {"type": "object", "additionalProperties": _PCT},
                "thresholds": {"type": "object", "additionalProperties": _MILLE},
                "hardLimits": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 1}},
                "rules": {"type": "object", "additionalProperties": {"type": "integer", "minimum": 0}},
            }}},
        "primaryTypePrecedence": {"type": "array", "items": {"enum": [t.value for t in DetectorType]},
                                  "uniqueItems": True},
        "normalisation": {"enum": ["FAMILY_P95"]},
        "grouping": {"type": "object", "additionalProperties": False, "required": ["scope"],
                     "properties": {"scope": {"enum": ["PRODUCT_SET", "PRODUCT_SET_AND_ACTION_CLASS"]}}},
    },
}

SCORING_PROFILE_JSON = {
    "type": "object", "additionalProperties": False,
    "required": ["applicability", "weights", "thresholds", "mandatoryDimensions"],
    "properties": {
        "description": {"type": "string", "maxLength": 2000},
        "applicability": APPLICABILITY,
        "weights": _weights(SCORE_FACTORS),
        "thresholds": {"type": "object", "additionalProperties": False, "required": ["recommended", "review"],
                       "properties": {"recommended": _PCT, "review": _PCT}},
        "belowReviewTreatment": {"enum": list(BELOW_REVIEW)},
        "mandatoryDimensions": {"type": "array", "items": {"enum": [d.value for d in Dimension]}, "uniqueItems": True},
    },
}

PRIORITY_POLICY_JSON = {
    "type": "object", "additionalProperties": False,
    "required": ["applicability", "weights", "bands", "overrides", "sunsetUrgency"],
    "properties": {
        "description": {"type": "string", "maxLength": 2000},
        "applicability": APPLICABILITY,
        "weights": _weights(PRIORITY_FACTORS),
        "bands": {"type": "array", "minItems": 4, "maxItems": 4, "items": {
            "type": "object", "additionalProperties": False, "required": ["band", "min"],
            "properties": {"band": {"enum": list(BANDS)}, "min": _PCT}}},
        "overrides": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["kind", "minimumBand"],
            "properties": {"kind": {"enum": list(OVERRIDE_KINDS)}, "minimumBand": {"enum": list(BANDS)},
                           "withinDays": {"type": "integer", "minimum": 1},
                           "minSubscriptions": {"type": "integer", "minimum": 1}}}},
        "sunsetUrgency": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["withinDays", "value"],
            "properties": {"withinDays": {"type": "integer", "minimum": 1}, "value": _PCT}}},
    },
}

POLICY_PACK_JSON = {
    "type": "object", "additionalProperties": False,
    "required": ["applicability", "gates", "overridable", "approvalRoutes", "auditRetentionYears"],
    "properties": {
        "description": {"type": "string", "maxLength": 2000},
        "applicability": APPLICABILITY,
        "thresholds": {"type": "object", "additionalProperties": False,
                       "properties": {"recommended": _PCT, "review": _PCT}},
        "gates": {
            "type": "object", "additionalProperties": False,
            "required": ["minimumEvidenceCoverage", "missingEvidenceTreatment", "criticalConflictTreatment",
                         "staleEvidenceTreatment", "technicalEquivalenceGate", "executionReadinessGate",
                         "humanReviewRequired"],
            "properties": {
                "minimumEvidenceCoverage": _PCT, "missingEvidenceTreatment": {"enum": list(MISSING_EVIDENCE)},
                "criticalConflictTreatment": {"enum": list(CRITICAL_CONFLICT)},
                "staleEvidenceTreatment": {"enum": list(STALE_EVIDENCE)},
                "technicalEquivalenceGate": {"enum": list(TECH_GATE)},
                "executionReadinessGate": {"enum": list(EXEC_GATE)},
                "humanReviewRequired": {"type": "boolean"},
            }},
        "overridable": {"type": "array", "items": {"enum": list(TELCO_DEFAULTS)}, "uniqueItems": True},
        "approvalRoutes": {"type": "array", "minItems": 1, "items": {
            "type": "object", "additionalProperties": False, "required": ["dispositions", "scopes"],
            "properties": {
                "dispositions": {"type": "array", "items": {"enum": list(DISPOSITIONS)}, "minItems": 1},
                "minMrr": {"type": "integer", "minimum": 0},
                "minSubscriptions": {"type": "integer", "minimum": 0},
                "scopes": {"type": "array", "items": {"enum": list(APPROVAL_SCOPES)}, "minItems": 1,
                           "uniqueItems": True},
                "reason": {"type": "string", "maxLength": 200}}}},
        "allowSelfApproval": {"type": "boolean"},
        "auditRetentionYears": {"type": "integer", "minimum": 1, "maximum": 30},
    },
}


def _sum_100(errors: list[FieldError], path: str, weights: dict[str, int]) -> None:
    total = sum(weights.values())
    if total != 100:
        errors.append(FieldError(path, f"Weights total {total}%, they must total 100%"))


def check_detector_pack(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    seen = set()
    for i, d in enumerate(content["detectors"]):
        t = DetectorType(d["type"])
        if t in seen:
            errors.append(FieldError(f"detectors[{i}].type", f"{t.value} is listed twice"))
        seen.add(t)
        expected = set(DETECTOR_FACTORS[t])
        if expected:
            got = set(d.get("factors", {}))
            if got != expected:
                errors.append(FieldError(f"detectors[{i}].factors",
                                         f"{t.value} factors must be exactly: {', '.join(DETECTOR_FACTORS[t])}"))
            else:
                _sum_100(errors, f"detectors[{i}].factors", d["factors"])
            if not d.get("thresholds"):
                errors.append(FieldError(f"detectors[{i}].thresholds", f"{t.value} needs thresholds"))
    th = next((d.get("thresholds", {}) for d in content["detectors"] if d["type"] == "DUPLICATE"), {})
    if th and not (th.get("probable", 0) < th.get("strong", 0)):
        errors.append(FieldError("detectors", "DUPLICATE thresholds need probable < strong"))
    if set(content["primaryTypePrecedence"]) != {t.value for t in seen}:
        errors.append(FieldError("primaryTypePrecedence", "List every detector of the pack exactly once"))
    return errors


def check_scoring_profile(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    _sum_100(errors, "weights", content["weights"])
    t = content["thresholds"]
    if t["recommended"] <= t["review"]:
        errors.append(FieldError("thresholds", "The recommended threshold must be above the review threshold"))
    return errors


def check_priority_policy(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    _sum_100(errors, "weights", content["weights"])
    bands = content["bands"]
    if [b["band"] for b in bands] != list(BANDS) or bands[0]["min"] != 0 or \
            any(bands[i]["min"] >= bands[i + 1]["min"] for i in range(len(bands) - 1)):
        errors.append(FieldError("bands", "Bands must be LOW, MEDIUM, HIGH, CRITICAL with rising minimums from 0"))
    return errors


def check_policy_pack(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    t = content.get("thresholds") or {}
    if "recommended" in t and "review" in t and t["recommended"] <= t["review"]:
        errors.append(FieldError("thresholds", "The recommended threshold must be above the review threshold"))
    return errors


# ---------------------------------------------------------------------------
# Recommendation of packs
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class RunContext:
    markets: frozenset[str]
    families: frozenset[str]
    objectives: frozenset[str]
    canonical: str | None = None
    mandatory_dimensions: frozenset[str] = frozenset(d.value for d in Dimension)


@dataclass(frozen=True)
class Candidate:
    key: str
    ref: str
    version: tuple[int, int]
    content: dict[str, Any]


@dataclass
class Ranked:
    ref: str
    eligible: bool
    reasons: list[str]
    objective_coverage: int
    dimension_coverage: int
    specificity: int
    priority: int
    version: tuple[int, int]

    def rank_key(self) -> tuple:
        return (self.objective_coverage, self.specificity, self.priority, self.version)


@dataclass
class Recommendation:
    chosen: str | None
    tie: list[str]
    ranking: list[Ranked]
    trace: list[str] = field(default_factory=list)


def _rank(c: Candidate, ctx: RunContext) -> Ranked:
    a = c.content["applicability"]
    reasons: list[str] = []
    markets, families = set(a["markets"]), set(a["productFamilies"])
    if "GLOBAL" not in markets and not ctx.markets <= markets:
        reasons.append(f"does not cover markets {', '.join(sorted(ctx.markets - markets))}")
    if "ALL" not in families and not ctx.families <= families:
        reasons.append(f"does not cover product families {', '.join(sorted(ctx.families - families))}")
    if ctx.canonical and a.get("canonicalSchema") and a["canonicalSchema"] != ctx.canonical:
        reasons.append(f"built for schema {a['canonicalSchema']}, run uses {ctx.canonical}")
    dims = set(a["dimensions"])
    dim_cov = 100 * len(ctx.mandatory_dimensions & dims) // len(ctx.mandatory_dimensions) \
        if ctx.mandatory_dimensions else 100
    if dim_cov < 100:
        reasons.append(f"mandatory dimensions not covered: {', '.join(sorted(ctx.mandatory_dimensions - dims))}")
    obj_cov = 100 * len(ctx.objectives & set(a["objectives"])) // len(ctx.objectives) if ctx.objectives else 100
    specificity = (2 if "GLOBAL" not in markets else 0) + (1 if "ALL" not in families else 0)
    return Ranked(c.ref, not reasons, reasons, obj_cov, dim_cov, specificity, a.get("priority", 0), c.version)


def recommend(candidates: list[Candidate], ctx: RunContext, label: str) -> Recommendation:
    """Filter, then rank by objective coverage, specificity, priority, version. Ties are not broken."""
    ranked = sorted((_rank(c, ctx) for c in candidates),
                    key=lambda r: (not r.eligible, -r.objective_coverage, -r.specificity, -r.priority,
                                   -r.version[0], -r.version[1], r.ref))
    eligible = [r for r in ranked if r.eligible]
    trace = [f"{label}: {len(candidates)} published, {len(eligible)} eligible for the run's markets, families and "
             f"mandatory dimensions"]
    for r in ranked:
        if not r.eligible:
            trace.append(f"{r.ref} excluded: {'; '.join(r.reasons)}")
    if not eligible:
        return Recommendation(None, [], ranked, trace + ["No eligible candidate"])
    top = eligible[0]
    tie = [r.ref for r in eligible if r.rank_key() == top.rank_key()]
    if len(tie) > 1:
        trace.append(f"Tie between {', '.join(tie)} on objective coverage, specificity, priority and version: "
                     "choose one")
        return Recommendation(None, tie, ranked, trace)
    trace.append(f"Recommended {top.ref}: objectives {top.objective_coverage}%, specificity {top.specificity}, "
                 f"priority {top.priority}")
    return Recommendation(top.ref, [], ranked, trace)


# ---------------------------------------------------------------------------
# Settings precedence
# ---------------------------------------------------------------------------

def resolve_settings(scoring: dict | None, policy: dict | None, overrides: dict[str, Any]) -> dict[str, dict]:
    """value + source for each setting: run override -> policy pack -> scoring profile -> Telco default."""
    out: dict[str, dict] = {}
    for name, default in TELCO_DEFAULTS.items():
        value, source = default, "Telco default"
        if scoring:
            sp = {"recommendedThreshold": scoring["thresholds"]["recommended"],
                  "reviewThreshold": scoring["thresholds"]["review"],
                  "belowReviewTreatment": scoring.get("belowReviewTreatment")}.get(name)
            if sp is not None:
                value, source = sp, "Scoring profile"
        if policy:
            pp = {**policy["gates"], **{"recommendedThreshold": (policy.get("thresholds") or {}).get("recommended"),
                                        "reviewThreshold": (policy.get("thresholds") or {}).get("review")}}.get(name)
            if pp is not None:
                value, source = pp, "Policy pack"
        if name in overrides:
            value, source = overrides[name], "Run override"
        out[name] = {"value": value, "source": source}
    return out


def check_overrides(overrides: dict[str, Any], policy: dict | None, authorised: bool) -> list[FieldError]:
    errors: list[FieldError] = []
    allowed = set((policy or {}).get("overridable", []))
    for name, value in overrides.items():
        spec = SETTING_VALUES.get(name)
        path = f"overrides.{name}"
        if spec is None:
            errors.append(FieldError(path, "Unknown setting"))
            continue
        if name == "missingEvidenceTreatment" and value == "ASSUME_ZERO":
            errors.append(FieldError(path, "ASSUME_ZERO is prohibited: missing evidence is never scored low"))
            continue
        if spec[0] == "int":
            if not isinstance(value, int) or isinstance(value, bool) or not spec[1] <= value <= spec[2]:
                errors.append(FieldError(path, f"Enter a whole number from {spec[1]} to {spec[2]}"))
                continue
        elif spec[0] == "bool":
            if not isinstance(value, bool):
                errors.append(FieldError(path, "Must be true or false"))
                continue
        elif value not in spec:
            errors.append(FieldError(path, f"Must be one of {', '.join(spec)}"))
            continue
        if name not in allowed:
            errors.append(FieldError(path, "The policy pack does not allow runs to change this setting"))
        elif not authorised:
            errors.append(FieldError(path, "Only an authorised configurator (config.scoring) may override this"))
    return errors


# ---------------------------------------------------------------------------
# Compatibility
# ---------------------------------------------------------------------------

@dataclass
class Issue:
    id: str
    severity: str  # INFO | WARNING | BLOCKING
    area: str
    message: str


def status_of(issues: list[Issue]) -> str:
    if any(i.severity == "BLOCKING" for i in issues):
        return "BLOCKED"
    return "WARNING" if any(i.severity == "WARNING" for i in issues) else "PASS"


def compatibility(*, selections: dict[str, dict], settings: dict[str, dict], policy: dict | None,
                  snapshot: dict | None, readiness: dict | None, ai_routes: list[dict]) -> list[Issue]:
    """``selections``: role -> {ref, recommendation(Recommendation|None), objective_coverage}."""
    issues: list[Issue] = []
    labels = {"detectorPack": "Detector pack", "scoringProfile": "Scoring profile",
              "priorityPolicy": "Priority policy", "policyPack": "Policy pack"}
    for role, label in labels.items():
        sel = selections.get(role) or {}
        if not sel.get("ref"):
            tie = sel.get("tie") or []
            issues.append(Issue(f"SELECT:{role}", "BLOCKING", "Configuration",
                                f"Choose a {label.lower()}" + (f" ({' or '.join(tie)} rank equally)" if tie else
                                                               " (none is eligible for this scope)")))
        elif not sel.get("eligible", True):
            issues.append(Issue(f"INELIGIBLE:{role}", "BLOCKING", "Configuration",
                                f"{sel['ref']} does not fit this run: {'; '.join(sel.get('reasons', []))}"))
        elif sel.get("objective_coverage", 100) < 100:
            issues.append(Issue(f"OBJECTIVES:{role}", "WARNING", "Configuration",
                                f"{sel['ref']} covers {sel['objective_coverage']}% of the run's objectives"))
    rec, rev = settings["recommendedThreshold"]["value"], settings["reviewThreshold"]["value"]
    if rec <= rev:
        issues.append(Issue("THRESHOLDS", "BLOCKING", "Thresholds",
                            f"Recommendation threshold ({rec}) must be above the review threshold ({rev})"))
    if policy and policy["gates"]["humanReviewRequired"] and not settings["humanReviewRequired"]["value"]:
        issues.append(Issue("HUMAN_REVIEW", "BLOCKING", "Governance", "The policy pack requires human review"))
    if snapshot is None:
        issues.append(Issue("SNAPSHOT", "BLOCKING", "Evidence", "Approve an evidence snapshot before starting"))
    elif readiness:
        for d in readiness["domains"]:
            if d["selected"] and d["status"] != "AVAILABLE":
                sev = "WARNING"
                issues.append(Issue(f"EVIDENCE:{d['domain']}", sev, "Evidence",
                                    f"{d['label']} is {d['status_label'].lower()} ({d['coverage']}%): {d['consequence']}"))
    for r in ai_routes:
        if not r["compliant"]:
            issues.append(Issue(f"AI:{r['task']}", "BLOCKING", "AI route", f"{r['label']}: no approved model fits"))
    if ai_routes and all(not r["chain"] for r in ai_routes):
        issues.append(Issue("AI:NO_LLM", "INFO", "AI route", "Templates only: no model will be called"))
    return issues


def warnings_to_acknowledge(issues: list[Issue]) -> list[str]:
    return sorted(i.id for i in issues if i.severity == "WARNING")
