"""AI runtime (BUILD_PLAN step 8, screen 16). Pure: registry content schemas, governance checks, route planning.

No model or provider is hard-coded. Approved deployments are registry artifacts (kind AI_DEPLOYMENT) and the
tenant's runtime profile (kind AI_RUNTIME_PROFILE) chooses a strategy, a routing policy and per-task routes. For
each task the planner returns the deployments to try in order (at most 2 models) followed by the deterministic
template, plus the governance checks that decided eligibility. The LLM only ever produces suggestions,
explanations and drafts (SPEC_NOTES global rules); the runtime never computes scores, routes or defaults with it.
"""

import re
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum
from typing import Any

from app.domain.common import FieldError


class Strategy(StrEnum):
    CUSTOMER_REGISTRY = "CUSTOMER_REGISTRY"
    PLATFORM_MANAGED = "PLATFORM_MANAGED"
    BYO_ENDPOINT = "BYO_ENDPOINT"
    NO_LLM = "NO_LLM"


class RoutingPolicy(StrEnum):
    QUALITY_FIRST = "QUALITY_FIRST"
    COST_OPTIMISED = "COST_OPTIMISED"
    CUSTOMER_HOSTED_ONLY = "CUSTOMER_HOSTED_ONLY"
    TASK_SPECIFIC = "TASK_SPECIFIC"
    NO_LLM = "NO_LLM"


class FailureTreatment(StrEnum):
    DETERMINISTIC_FALLBACK = "DETERMINISTIC_FALLBACK"
    ALTERNATE_APPROVED_MODEL = "ALTERNATE_APPROVED_MODEL"
    HUMAN_REVIEW = "HUMAN_REVIEW"
    BLOCK_AI_EXPLANATION = "BLOCK_AI_EXPLANATION"


class Hosting(StrEnum):
    PLATFORM = "PLATFORM"  # the model the platform is configured with (standalone: PRA_AI_PROVIDER; studio: studio)
    CUSTOMER_REGISTRY = "CUSTOMER_REGISTRY"  # a model from the customer's own approved registry
    CUSTOMER_HOSTED = "CUSTOMER_HOSTED"  # a customer-hosted endpoint implementing the governed AI contract


class Task(StrEnum):
    MAPPING_SUGGESTION = "MAPPING_SUGGESTION"
    PRODUCT_MATCHING = "PRODUCT_MATCHING"
    RECOMMENDATION_REASONING = "RECOMMENDATION_REASONING"
    TECHNICAL_EXPLANATION = "TECHNICAL_EXPLANATION"
    DECISION_RATIONALE = "DECISION_RATIONALE"
    TRANSITION_NARRATIVE = "TRANSITION_NARRATIVE"


TASK_LABELS = {
    Task.MAPPING_SUGGESTION: "Mapping suggestions for ambiguous columns",
    Task.PRODUCT_MATCHING: "Product matching explanation",
    Task.RECOMMENDATION_REASONING: "Recommendation reasoning",
    Task.TECHNICAL_EXPLANATION: "Technical dependency explanation",
    Task.DECISION_RATIONALE: "Decision rationale draft",
    Task.TRANSITION_NARRATIVE: "Transition narrative",
}
STRATEGY_HOSTING = {
    Strategy.PLATFORM_MANAGED: {Hosting.PLATFORM},
    Strategy.CUSTOMER_REGISTRY: {Hosting.CUSTOMER_REGISTRY},
    Strategy.BYO_ENDPOINT: {Hosting.CUSTOMER_HOSTED},
    Strategy.NO_LLM: set(),
}
PROVIDERS = ("platform", "anthropic", "byo_http")
DATA_CLASSES = ("METADATA", "AGGREGATES", "CONFIDENTIAL")
MAX_MODELS = 2

_KEY = {"type": "string", "pattern": r"^[A-Z][A-Z0-9-]{1,63}(@\d+\.\d+)?$"}

AI_DEPLOYMENT_JSON = {
    "type": "object",
    "required": ["provider", "displayName", "hosting", "region", "dataClassifications", "capabilities"],
    "additionalProperties": False,
    "properties": {
        "description": {"type": "string", "maxLength": 2000},
        "provider": {"enum": list(PROVIDERS)},
        "model": {"type": "string", "minLength": 1, "maxLength": 120},
        "displayName": {"type": "string", "minLength": 1, "maxLength": 120},
        "hosting": {"enum": [h.value for h in Hosting]},
        "endpoint": {"type": "string", "maxLength": 500},
        "secretRef": {"type": "string", "pattern": r"^[a-z0-9][a-z0-9-]{1,62}$"},
        "region": {"type": "string", "pattern": r"^[A-Z]{2,10}$"},
        "dataClassifications": {"type": "array", "items": {"enum": list(DATA_CLASSES)}, "minItems": 1,
                                "uniqueItems": True},
        "capabilities": {"type": "object", "additionalProperties": False, "required": ["structuredOutput"],
                         "properties": {"structuredOutput": {"type": "boolean"}, "toolUse": {"type": "boolean"}}},
        "qualityTier": {"type": "integer", "minimum": 1, "maximum": 5},
        "costTier": {"type": "integer", "minimum": 1, "maximum": 5},
        "latencyMsP95": {"type": "integer", "minimum": 1, "maximum": 600000},
        "maxOutputTokens": {"type": "integer", "minimum": 256, "maximum": 128000},
        "expiresOn": {"type": "string", "format": "date", "pattern": r"^\d{4}-\d{2}-\d{2}$"},
    },
}

AI_RUNTIME_PROFILE_JSON = {
    "type": "object",
    "required": ["strategy", "routingPolicy", "failureTreatment", "constraints", "routes"],
    "additionalProperties": False,
    "properties": {
        "description": {"type": "string", "maxLength": 2000},
        "strategy": {"enum": [s.value for s in Strategy]},
        "routingPolicy": {"enum": [r.value for r in RoutingPolicy]},
        "failureTreatment": {"enum": [f.value for f in FailureTreatment]},
        "aiConfidenceThreshold": {"type": "integer", "minimum": 0, "maximum": 100},
        "maxRetries": {"type": "integer", "minimum": 0, "maximum": 2},
        "constraints": {
            "type": "object", "additionalProperties": False, "required": ["dataClassification"],
            "properties": {
                "allowedRegions": {"type": "array", "items": {"type": "string", "pattern": r"^[A-Z]{2,10}$"},
                                   "uniqueItems": True},
                "dataClassification": {"enum": list(DATA_CLASSES)},
                "maxCostTier": {"type": "integer", "minimum": 1, "maximum": 5},
                "maxLatencyMs": {"type": "integer", "minimum": 1, "maximum": 600000},
                "allowToolUse": {"type": "boolean"},
            },
        },
        "routes": {
            "type": "array", "maxItems": len(Task),
            "items": {"type": "object", "required": ["task"], "additionalProperties": False,
                      "properties": {"task": {"enum": [t.value for t in Task]},
                                     "mode": {"enum": ["LLM", "DETERMINISTIC"]},
                                     "primary": _KEY, "fallback": _KEY}},
        },
    },
}

_URL = re.compile(r"^(https://[^\s/]+|http://(localhost|127\.0\.0\.1)(:\d+)?)(/[^\s]*)?$")


def check_ai_deployment(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    provider, hosting = content["provider"], Hosting(content["hosting"])
    if provider != "platform" and not content.get("model"):
        errors.append(FieldError("model", f"Provider {provider} needs a model"))
    if provider == "platform" and hosting is not Hosting.PLATFORM:
        errors.append(FieldError("hosting", "The platform provider is always PLATFORM hosted"))
    if provider == "byo_http":
        if hosting is not Hosting.CUSTOMER_HOSTED:
            errors.append(FieldError("hosting", "A bring-your-own endpoint is CUSTOMER_HOSTED"))
        if not _URL.match(content.get("endpoint", "")):
            errors.append(FieldError("endpoint", "Use an https:// URL (http:// only for localhost)"))
    elif "endpoint" in content:
        errors.append(FieldError("endpoint", "Only byo_http deployments have an endpoint"))
    if provider in ("anthropic", "byo_http") and hosting is Hosting.PLATFORM:
        errors.append(FieldError("hosting", "Only the platform provider is PLATFORM hosted"))
    return errors


def check_ai_runtime_profile(content: dict) -> list[FieldError]:
    errors: list[FieldError] = []
    seen: set[str] = set()
    strategy = Strategy(content["strategy"])
    if (strategy is Strategy.NO_LLM) != (content["routingPolicy"] == RoutingPolicy.NO_LLM):
        errors.append(FieldError("routingPolicy", "NO_LLM strategy and NO_LLM routing go together"))
    for i, r in enumerate(content["routes"]):
        if r["task"] in seen:
            errors.append(FieldError(f"routes[{i}].task", f"{r['task']} is routed twice"))
        seen.add(r["task"])
        if r.get("fallback") and not r.get("primary"):
            errors.append(FieldError(f"routes[{i}].fallback", "A fallback needs a primary deployment"))
        if r.get("fallback") and r.get("fallback") == r.get("primary"):
            errors.append(FieldError(f"routes[{i}].fallback", "The fallback must be a different deployment"))
    return errors


# ---------------------------------------------------------------------------
# Planning
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Deployment:
    key: str
    ref: str  # KEY@major.minor
    digest: str
    status: str  # artifact version status (PUBLISHED, DEPRECATED, ...)
    content: dict[str, Any]

    @property
    def hosting(self) -> Hosting:
        return Hosting(self.content["hosting"])


@dataclass
class Check:
    key: str
    label: str
    status: str  # PASS | WARNING | FAIL
    message: str


@dataclass
class RoutePlan:
    task: str
    label: str
    chain: list[Deployment]  # LLM deployments to try in order; the deterministic template always follows
    checks: list[Check]
    rejected: dict[str, list[str]]  # deployment key -> reasons it was not eligible
    trace: list[str] = field(default_factory=list)

    @property
    def compliant(self) -> bool:
        return all(c.status != "FAIL" for c in self.checks)

    @property
    def deterministic_only(self) -> bool:
        return not self.chain


def ineligibility(dep: Deployment, profile: dict, today: date) -> list[str]:
    """Governance reasons why ``dep`` may not serve this profile (empty = eligible)."""
    c = dep.content
    cons = profile["constraints"]
    strategy = Strategy(profile["strategy"])
    reasons = []
    if dep.status != "PUBLISHED":
        reasons.append(f"not approved ({dep.status.lower()})")
    if c.get("expiresOn") and date.fromisoformat(c["expiresOn"]) < today:
        reasons.append(f"approval expired on {c['expiresOn']}")
    if dep.hosting not in STRATEGY_HOSTING[strategy]:
        reasons.append(f"{dep.hosting.value.lower().replace('_', ' ')} deployment does not fit strategy "
                       f"{strategy.value}")
    if profile["routingPolicy"] == RoutingPolicy.CUSTOMER_HOSTED_ONLY and dep.hosting is Hosting.PLATFORM:
        reasons.append("routing policy allows customer-hosted models only")
    regions = cons.get("allowedRegions") or []
    if regions and c["region"] not in regions:
        reasons.append(f"region {c['region']} is not allowed ({', '.join(regions)})")
    if cons["dataClassification"] not in c["dataClassifications"]:
        reasons.append(f"not approved for {cons['dataClassification'].lower()} data")
    if not c["capabilities"].get("structuredOutput"):
        reasons.append("no structured (JSON schema) output")
    if c["capabilities"].get("toolUse") and not cons.get("allowToolUse", False):
        reasons.append("tool use is enabled but not permitted")
    if cons.get("maxCostTier") and c.get("costTier", 1) > cons["maxCostTier"]:
        reasons.append(f"cost tier {c.get('costTier')} above the budget ({cons['maxCostTier']})")
    if cons.get("maxLatencyMs") and c.get("latencyMsP95", 0) > cons["maxLatencyMs"]:
        reasons.append(f"p95 latency {c.get('latencyMsP95')} ms above {cons['maxLatencyMs']} ms")
    return reasons


def _lookup(ref_or_key: str, deployments: dict[str, Deployment]) -> Deployment | None:
    return deployments.get(ref_or_key) or deployments.get(ref_or_key.split("@")[0])


def plan_route(profile: dict, task: str, deployments: dict[str, Deployment], today: date) -> RoutePlan:
    """Deterministic: the deployments to try for ``task`` (max 2) and why. ``deployments`` is keyed by key and ref."""
    t = Task(task)
    plan = RoutePlan(task=t.value, label=TASK_LABELS[t], chain=[], checks=[], rejected={})
    route = next((r for r in profile["routes"] if r["task"] == t.value), {})
    policy = RoutingPolicy(profile["routingPolicy"])
    strategy = Strategy(profile["strategy"])
    unique = {d.key: d for d in deployments.values()}

    if strategy is Strategy.NO_LLM or policy is RoutingPolicy.NO_LLM:
        plan.trace.append("No-LLM mode: rules, scoring and templates only")
    elif route.get("mode") == "DETERMINISTIC":
        plan.trace.append(f"{t.value} is set to deterministic execution")
    else:
        if policy in (RoutingPolicy.TASK_SPECIFIC, RoutingPolicy.CUSTOMER_HOSTED_ONLY) and route.get("primary"):
            wanted = [route["primary"]] + ([route["fallback"]] if route.get("fallback") else [])
            plan.trace.append(f"Task route: {' then '.join(wanted)}")
            candidates = []
            for w in wanted:
                d = _lookup(w, deployments)
                if d is None:
                    plan.rejected[w] = ["no such deployment in the registry"]
                else:
                    candidates.append(d)
        else:
            order = {RoutingPolicy.COST_OPTIMISED: lambda d: (d.content.get("costTier", 3),
                                                              -d.content.get("qualityTier", 3), d.key)}
            key = order.get(policy, lambda d: (-d.content.get("qualityTier", 3), d.content.get("costTier", 3), d.key))
            candidates = sorted(unique.values(), key=key)
            plan.trace.append(f"{policy.value}: ranked {len(candidates)} registry deployments")
        for d in candidates:
            reasons = ineligibility(d, profile, today)
            if reasons:
                plan.rejected[d.key] = reasons
                continue
            if len(plan.chain) < MAX_MODELS and d.key not in {x.key for x in plan.chain}:
                plan.chain.append(d)
        plan.trace.append("Chosen: " + (", ".join(d.ref for d in plan.chain) or "none") + " → deterministic template")

    plan.checks = _route_checks(profile, plan, route)
    return plan


def _route_checks(profile: dict, plan: RoutePlan, route: dict) -> list[Check]:
    det = plan.deterministic_only
    llm_wanted = Strategy(profile["strategy"]) is not Strategy.NO_LLM and route.get("mode") != "DETERMINISTIC"
    checks = [
        Check("APPROVED", "Approved", "PASS" if not llm_wanted or plan.chain else "FAIL",
              "Deterministic execution" if not llm_wanted else
              ("Approved deployment: " + plan.chain[0].ref if plan.chain else
               "No approved, eligible deployment: " + "; ".join(f"{k}: {', '.join(v)}"
                                                                for k, v in plan.rejected.items()))),
        Check("STRUCTURED_OUTPUT", "Structured output", "PASS",
              "Template output" if det else "JSON schema enforced and validated"),
        Check("CITATIONS", "Evidence citations", "PASS",
              "Template cites evidence ids" if det else "Every cited id is checked; invalid output is retried"),
        Check("RESIDENCY", "Residency", "PASS",
              "No data leaves the agent" if det else
              "Regions: " + ", ".join(sorted({d.content["region"] for d in plan.chain}))),
        Check("FALLBACK", "Fallback", "PASS", "Deterministic template after "
              + (f"{len(plan.chain)} model(s)" if plan.chain else "rules")),
    ]
    if (llm_wanted and FailureTreatment(profile["failureTreatment"]) is FailureTreatment.ALTERNATE_APPROVED_MODEL
            and len(plan.chain) < 2):
        checks[-1] = Check("FALLBACK", "Fallback", "WARNING",
                           "No eligible secondary model; the deterministic template is used instead")
    return checks


def plan_all(profile: dict, deployments: dict[str, Deployment], today: date) -> list[RoutePlan]:
    return [plan_route(profile, t.value, deployments, today) for t in Task]


def pinned_routes(plans: list[RoutePlan]) -> dict[str, list[dict[str, str]]]:
    """What a run pins: per task, the exact deployment refs + digests in order (empty = deterministic)."""
    return {p.task: [{"ref": d.ref, "digest": d.digest} for d in p.chain] for p in plans}
