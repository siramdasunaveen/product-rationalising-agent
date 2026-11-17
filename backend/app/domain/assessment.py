"""Assessment (BUILD_PLAN step 11). Pure and deterministic; no LLM computes anything here.

For one normalized candidate and the facts of the run's pinned snapshot this module computes:
- the Opportunity Score (pinned Scoring Profile weights over five 0..1 inputs, ROUND half up),
- the three assessment dimensions COMMERCIAL / TECHNICAL / EXECUTION (score 0-100 or None + status),
- the guardrail state (RECOMMENDED / REVIEW_REQUIRED / BELOW_REVIEW / NOT_ASSESSED / BLOCKED),
- the deterministic recommendation (action, target, conditions, reason codes, template text) with the
  disposition guards of CLAUDE.md rules 3-5,
- impact facts (exact values + display text), the priority index, bands and overrides (separate from the score),
- blocker drafts (EVIDENCE / VALIDATION / DEPENDENCY) and the derived Candidate Status.

Missing evidence is never a low value (rule 1): an input without evidence is None; weighted values re-normalise
over the assessed inputs and need a minimum assessed weight, otherwise the result is None / NOT_ASSESSED.
Similarity, Opportunity Score, Priority and AI confidence stay separate values.
"""

from dataclasses import dataclass, field
from datetime import date, timedelta
from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from app.domain.detectors import LEGACY_LIFECYCLE, ProductFeatures, duplicate_factors, jaccard, weighted

SCORE_INPUTS = ("detectorSignal", "commercialValue", "technicalFeasibility", "operationalBenefit",
                "strategicAlignment")
PRIORITY_FACTORS = ("businessImpact", "exposure", "operationalBurden", "legacyUrgency", "regulatoryRisk",
                    "dependencyCriticality", "strategicAlignment")
BANDS = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
SEVERITY = ("LOW", "MEDIUM", "HIGH", "CRITICAL")
DIMENSIONS = ("COMMERCIAL", "TECHNICAL", "EXECUTION")
PAIR_TYPES = ("DUPLICATE", "OVERLAP")

# Defaults; a Scoring Profile / Priority Policy may override them ("dimensionRules", "factorRules").
DEFAULT_DIMENSION_RULES: dict[str, Any] = {
    "minAssessedWeight": 60,  # percent of input weight that must be assessed for a score
    "technicalEquivalencePass": 80,  # TECHNICAL score needed for "technically equivalent"
    "COMMERCIAL": {"domains": ["CRM_CATALOG", "CPQ", "PRICE_BOOK", "LIFECYCLE", "CUSTOMER_REVENUE_AGGREGATES"],
                   "mandatory": ["CRM_CATALOG"]},
    "TECHNICAL": {"domains": ["TECHNICAL_CATALOG", "PROVISIONING", "INVENTORY"],
                  "mandatory": ["TECHNICAL_CATALOG", "PROVISIONING"]},
    "EXECUTION": {"domains": ["INVENTORY", "CUSTOMER_REVENUE_AGGREGATES", "ORDER_BILLING_ASSURANCE", "LIFECYCLE"],
                  "mandatory": ["INVENTORY", "CUSTOMER_REVENUE_AGGREGATES"]},
}
DEFAULT_PRIORITY_RULES: dict[str, Any] = {
    "minAssessedWeight": 50,
    "legacyCurve": [{"withinDays": 730, "value": 40}],  # beyond the policy's sunsetUrgency table
    "noSunsetValue": 0,  # lifecycle evidence present but no sunset/end-of-sale date
}
OBJECTIVE_TYPES = {
    "REDUCE_DUPLICATES": {"DUPLICATE", "OVERLAP"},
    "BSS_SUNSET": {"LEGACY"},
    "REDUCE_FALLOUT": {"OPERATIONAL_BURDEN"},
    "IMPROVE_MARGIN": {"DUPLICATE", "OVERLAP", "COMPLEXITY"},
    "MIGRATION_READINESS": {"LEGACY"},
    "PORTFOLIO_STANDARDISATION": {"DUPLICATE", "OVERLAP", "COMPLEXITY"},
}
COMPATIBILITY = {"COMPATIBLE": 1.0, "PARTIAL": 0.5, "INCOMPATIBLE": 0.0}
OBSOLETE_STATUS = {"WITHDRAWN", "INACTIVE"}
REDUCTION = {"CONSOLIDATE": 100, "RETIRE": 100, "MIGRATE": 80, "STANDARDISE": 60, "SIMPLIFY": 60, "RETAIN": 0}
EVIDENCE_DOMAIN_TO_BLOCKER_DOMAIN = {
    "CRM_CATALOG": "COMMERCIAL", "CPQ": "COMMERCIAL", "PRICE_BOOK": "COMMERCIAL", "LIFECYCLE": "COMMERCIAL",
    "CUSTOMER_REVENUE_AGGREGATES": "COMMERCIAL", "TECHNICAL_CATALOG": "ARCHITECTURE", "PROVISIONING": "PROVISIONING",
    "INVENTORY": "PROVISIONING", "ORDER_BILLING_ASSURANCE": "BILLING",
}
OWNER_BY_DOMAIN = {"COMMERCIAL": "portfolio_manager", "ARCHITECTURE": "enterprise_architect",
                   "PROVISIONING": "enterprise_architect", "BILLING": "evidence_owner", "DATA": "data_steward",
                   "RISK": "portfolio_manager", "REGULATORY": "portfolio_manager", "SECURITY": "agent_admin"}
SLA_DAYS = {"CRITICAL": 5, "HIGH": 10, "MEDIUM": 20, "LOW": 30}
CURRENCY_SYMBOL = {"GBP": "£", "EUR": "€", "USD": "$"}


def round_half_up(x: float) -> int:
    return int(Decimal(repr(x)).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def mille(x: float | None) -> int | None:
    return None if x is None else max(0, min(1000, round_half_up(x * 1000)))


def percentile(value: float, population: list[float]) -> float:
    """Mid-rank percentile of ``value`` in ``population`` (0..1)."""
    if not population:
        return 0.0
    below = sum(1 for v in population if v < value)
    equal = sum(1 for v in population if v == value)
    return (below + 0.5 * equal) / len(population)


def _mean(values: list[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return sum(vs) / len(vs) if vs else None


def _max(values: list[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return max(vs) if vs else None


# ---------------------------------------------------------------------------
# Inputs
# ---------------------------------------------------------------------------

@dataclass
class Context:
    """Run-wide facts the assessment of every candidate shares."""

    products: dict[str, ProductFeatures]  # source products in scope
    coverage: dict[str, set[str]]  # product id -> evidence domains that cover it in the snapshot
    stale_domains: set[str]
    conflicted: dict[str, list[str]]  # product id -> disputed (deferred) material fields "Entity.field"
    objectives: set[str]
    settings: dict[str, Any]  # resolved settings from the Run Manifest (name -> value)
    scoring: dict[str, Any]  # pinned Scoring Profile content
    priority: dict[str, Any]  # pinned Priority Policy content
    dispositions: dict[str, list[str]]  # readiness policy: disposition -> domains that must be AVAILABLE
    domain_importance: dict[str, str]  # evidence domain -> CRITICAL/HIGH/MEDIUM/LOW
    domain_labels: dict[str, str]
    as_of: date  # snapshot approval date
    family_fallout: dict[str | None, float | None] = field(default_factory=dict)  # family -> baseline rate

    def rules(self) -> dict[str, Any]:
        return {**DEFAULT_DIMENSION_RULES, **(self.scoring.get("dimensionRules") or {})}

    def priority_rules(self) -> dict[str, Any]:
        return {**DEFAULT_PRIORITY_RULES, **(self.priority.get("factorRules") or {})}


@dataclass
class CandidateInput:
    code: str
    subjects: list[str]
    primary_type: str
    supporting_types: list[str]
    findings: list[dict[str, Any]]  # {type, signal (per mille), factors, evidence_ids}


# ---------------------------------------------------------------------------
# Product helpers
# ---------------------------------------------------------------------------

def obsolete(p: ProductFeatures) -> bool:
    return (p.lifecycle in LEGACY_LIFECYCLE) or (p.status or "").upper() in OBSOLETE_STATUS


def footprint(p: ProductFeatures) -> bool | None:
    """Active technical or customer footprint; None when neither inventory nor customer evidence exists."""
    known = [x for x in (p.inventory_active, p.subscriptions) if x is not None]
    if not known:
        return None
    return any(x > 0 for x in known)


def eligible_target(p: ProductFeatures) -> bool:
    return (p.status or "ACTIVE").upper() == "ACTIVE" and p.lifecycle not in LEGACY_LIFECYCLE


def legacy_architecture(p: ProductFeatures) -> bool:
    return bool(p.legacy_billing) or any(w.get("legacy") for w in p.workflows) or bool(p.inventory_legacy)


def compatibility(p: ProductFeatures) -> float | None:
    vals = [COMPATIBILITY[w["compatibility"]] for w in p.workflows if w.get("compatibility") in COMPATIBILITY]
    return min(vals) if vals else None


def select_target(c: CandidateInput, ctx: Context) -> tuple[str | None, str, list[str]]:
    """(target, source, reasons) for a pair candidate. Deterministic; never invents a preference."""
    a, b = (ctx.products[s] for s in c.subjects)
    if a.replacement == b.product_id and eligible_target(b):
        return b.product_id, a.product_id, ["REPLACEMENT_DECLARED"]
    if b.replacement == a.product_id and eligible_target(a):
        return a.product_id, b.product_id, ["REPLACEMENT_DECLARED"]
    ok = [p for p in (a, b) if eligible_target(p)]
    if not ok:
        return None, a.product_id, ["NO_ELIGIBLE_TARGET"]
    if len(ok) == 1:
        src = b if ok[0] is a else a
        return ok[0].product_id, src.product_id, ["ONLY_ELIGIBLE_TARGET"]
    ranked = sorted(ok, key=lambda p: (-(p.subscriptions if p.subscriptions is not None else -1),
                                       legacy_architecture(p), p.product_id))
    src = b if ranked[0] is a else a
    return ranked[0].product_id, src.product_id, ["LARGER_ACTIVE_BASE" if ranked[0].subscriptions is not None
                                                 else "LOWEST_PRODUCT_ID"]


# ---------------------------------------------------------------------------
# Dimensions
# ---------------------------------------------------------------------------

def commercial_measure(c: CandidateInput, ctx: Context) -> float | None:
    if c.primary_type in PAIR_TYPES and len(c.subjects) == 2:
        a, b = (ctx.products[s] for s in c.subjects)
        f = duplicate_factors(a, b)
        sim, cov = weighted({k: f[k] for k in ("service", "cpq", "eligibility", "pricingStructure")},
                            {"service": 30, "cpq": 20, "eligibility": 15, "pricingStructure": 10})
        return sim if cov >= ctx.rules()["minAssessedWeight"] else None
    sig = [x["signal"] for x in c.findings if x["type"] == c.primary_type]
    return max(sig) / 1000 if sig else None


def technical_measure(c: CandidateInput, ctx: Context) -> float | None:
    ps = [ctx.products[s] for s in c.subjects]
    compat = _mean([compatibility(p) for p in ps])
    if len(ps) == 2:
        a, b = ps
        sim = jaccard(a.services | a.resources, b.services | b.resources) if a.services and b.services else None
        return _mean([sim, compat])
    return compat


def execution_measure(c: CandidateInput, ctx: Context, source: ProductFeatures) -> float | None:
    parts: list[float | None] = [compatibility(source)]
    if source.legacy_billing is not None:
        parts.append(0.5 if source.legacy_billing else 1.0)
    if source.protected_contracts is not None:
        parts.append(0.8 if source.protected_contracts > 0 else 1.0)
    if source.inventory_legacy is not None:
        parts.append(0.5 if source.inventory_legacy else 1.0)
    return _mean(parts)


def dimension(name: str, c: CandidateInput, ctx: Context, source: ProductFeatures) -> dict[str, Any]:
    rules = ctx.rules()[name]
    st = ctx.settings
    pairs = [(p, d) for p in c.subjects for d in rules["domains"]]
    covered = [(p, d) for p, d in pairs if d in ctx.coverage.get(p, set())]
    coverage = 100 * len(covered) // len(pairs) if pairs else 0
    missing = sorted({d for p in c.subjects for d in rules["mandatory"] if d not in ctx.coverage.get(p, set())})
    stale = sorted({d for p, d in covered if d in ctx.stale_domains})
    entities = {"COMMERCIAL": ("Product.", "ProductLifecycle.", "PriceBookEntry.", "CustomerRevenueAggregate."),
                "TECHNICAL": ("ProvisioningWorkflow.", "ServiceSpec."),
                "EXECUTION": ("InventoryAggregate.", "CustomerRevenueAggregate.", "BillingDependency.")}[name]
    conflicts = sorted({f for p in c.subjects for f in ctx.conflicted.get(p, []) if f.startswith(entities)})
    reasons: list[str] = []
    if missing:
        treat = st.get("missingEvidenceTreatment", "NOT_ASSESSED")
        status = {"BLOCK": "BLOCKED", "REVIEW_REQUIRED": "PARTIAL"}.get(treat, "NOT_ASSESSED")
        reasons.append(f"Mandatory evidence missing: {', '.join(missing)}")
    elif stale and st.get("staleEvidenceTreatment") == "BLOCK":
        status = "BLOCKED"
        reasons.append(f"Stale evidence: {', '.join(stale)}")
    elif conflicts and st.get("criticalConflictTreatment", "BLOCK") == "BLOCK":
        status = "BLOCKED"
        reasons.append(f"Unresolved material conflict: {', '.join(conflicts)}")
    else:
        status = "ASSESSED" if coverage >= st.get("minimumEvidenceCoverage", 80) else "PARTIAL"
        if stale:
            reasons.append(f"Refresh required: {', '.join(stale)}")
            status = "PARTIAL" if st.get("staleEvidenceTreatment", "REQUIRE_REFRESH") == "REQUIRE_REFRESH" else status
        if conflicts:
            reasons.append(f"Steward review of: {', '.join(conflicts)}")
            status = "PARTIAL"
        if coverage < st.get("minimumEvidenceCoverage", 80):
            reasons.append(f"Evidence coverage {coverage}% below {st.get('minimumEvidenceCoverage', 80)}%")
    score = None
    if status in ("ASSESSED", "PARTIAL"):
        m = {"COMMERCIAL": lambda: commercial_measure(c, ctx), "TECHNICAL": lambda: technical_measure(c, ctx),
             "EXECUTION": lambda: execution_measure(c, ctx, source)}[name]()
        if m is None:
            status = "NOT_ASSESSED"
            reasons.append("No measurable evidence for this dimension")
        else:
            score = round_half_up(100 * m)
    return {"status": status, "score": score, "coverage": coverage, "missingDomains": missing, "staleDomains": stale,
            "conflicts": conflicts, "reasons": reasons}


# ---------------------------------------------------------------------------
# Opportunity Score
# ---------------------------------------------------------------------------

def strategic_alignment(c: CandidateInput, ctx: Context) -> float:
    if any(c.primary_type in OBJECTIVE_TYPES.get(o, set()) for o in ctx.objectives):
        return 1.0
    if any(t in OBJECTIVE_TYPES.get(o, set()) for o in ctx.objectives for t in c.supporting_types):
        return 0.7
    return 0.4


def fallout_multiple(p: ProductFeatures, ctx: Context) -> float | None:
    if p.fallout is None or not p.orders:
        return None
    base = ctx.family_fallout.get(p.family)
    if not base:
        return None
    return (p.fallout / p.orders) / base


def score_inputs(c: CandidateInput, ctx: Context, source: ProductFeatures) -> dict[str, float | None]:
    """The five 0..1 inputs. Commercial value is the revenue (else customer base) of the product being changed,
    as a percentile of the run's source products; None without customer/revenue evidence."""
    ps = [ctx.products[s] for s in c.subjects]
    primary = [x["signal"] for x in c.findings if x["type"] == c.primary_type]
    commercial = None
    if source.mrr is not None:
        commercial = percentile(source.mrr, [p.mrr for p in ctx.products.values() if p.mrr is not None])
    elif source.subscriptions is not None:
        commercial = percentile(float(source.subscriptions), [float(p.subscriptions) for p in ctx.products.values()
                                                              if p.subscriptions is not None])
    burden = [x["signal"] / 1000 for x in c.findings if x["type"] in ("OPERATIONAL_BURDEN", "COMPLEXITY")]
    fms = [fallout_multiple(p, ctx) for p in ps]
    operational = _max(burden + [min(1.0, fm / 3) if fm is not None else None for fm in fms])
    return {
        "detectorSignal": max(primary) / 1000 if primary else None,
        "commercialValue": commercial,
        "technicalFeasibility": technical_measure(c, ctx),
        "operationalBenefit": operational,
        "strategicAlignment": strategic_alignment(c, ctx),
    }


def opportunity_score(inputs: dict[str, float | None], weights: dict[str, int],
                      min_assessed: int) -> dict[str, Any]:
    assessed = {k: v for k, v in inputs.items() if v is not None}
    total_w = sum(weights[k] for k in assessed)
    coverage = total_w * 100 // sum(weights.values())
    out = {k: {"value": mille(v), "weight": weights[k],
               "contribution": None if v is None else mille(v * weights[k] / 100)} for k, v in inputs.items()}
    if not total_w or coverage < min_assessed:
        return {"score": None, "inputs": out, "coverage": coverage}
    value = sum(weights[k] * v for k, v in assessed.items()) / total_w
    return {"score": round_half_up(100 * value), "inputs": out, "coverage": coverage}


# ---------------------------------------------------------------------------
# Recommendation and guardrail
# ---------------------------------------------------------------------------

def tech_gate_applies(gate: str, action: str | None) -> bool:
    if action in (None, "INVESTIGATE", "RETAIN"):
        return False
    return {"REQUIRED_FOR_RECOMMENDATION": True, "REQUIRED_FOR_CONSOLIDATE": action == "CONSOLIDATE",
            "REQUIRED_FOR_MIGRATE": action == "MIGRATE"}.get(gate, False)


def preliminary_action(c: CandidateInput, ctx: Context, dims: dict[str, dict]) -> dict[str, Any]:
    """Action, target, source and reason codes before the score guardrail is applied (rules 3, 4, 5)."""
    tech_pass = ctx.rules()["technicalEquivalencePass"]
    if dims["COMMERCIAL"]["status"] in ("NOT_ASSESSED", "BLOCKED"):
        return {"action": "INVESTIGATE", "target": None, "source": c.subjects[0],
                "reasons": ["COMMERCIAL_EVIDENCE_MISSING"], "preliminary": False}
    if c.primary_type in PAIR_TYPES and len(c.subjects) == 2:
        target, source, why = select_target(c, ctx)
        if target is None:
            return {"action": "INVESTIGATE", "target": None, "source": source, "reasons": why, "preliminary": False}
        if c.primary_type == "OVERLAP":
            return {"action": "SIMPLIFY", "target": target, "source": source, "reasons": ["OVERLAPPING_OFFERS"] + why,
                    "preliminary": False}
        tech = dims["TECHNICAL"]
        if tech["status"] in ("NOT_ASSESSED", "BLOCKED"):
            return {"action": "CONSOLIDATE", "target": target, "source": source, "preliminary": True,
                    "reasons": ["DUPLICATE_SIGNAL", "TECHNICAL_EQUIVALENCE_NOT_ASSESSED"] + why}
        if tech["score"] is not None and tech["score"] < tech_pass:
            src = ctx.products[source]
            action = "MIGRATE" if (obsolete(src) or legacy_architecture(src)) and footprint(src) else "STANDARDISE"
            return {"action": action, "target": target, "source": source, "preliminary": False,
                    "reasons": ["DUPLICATE_SIGNAL", "TECHNICALLY_DIFFERENT", "STANDARDISE_OR_MIGRATE_BEFORE_CONSOLIDATE"]
                    + why}
        return {"action": "CONSOLIDATE", "target": target, "source": source, "preliminary": False,
                "reasons": ["DUPLICATE_SIGNAL", "TECHNICAL_EQUIVALENCE_PASS"] + why}
    p = ctx.products[c.subjects[0]]
    if c.primary_type == "LEGACY":
        fp = footprint(p)
        target = p.replacement if p.replacement in ctx.products and eligible_target(ctx.products[p.replacement]) \
            else None
        if fp is None:
            return {"action": "INVESTIGATE", "target": None, "source": p.product_id, "preliminary": False,
                    "reasons": ["ACTIVE_FOOTPRINT_UNKNOWN"]}
        if obsolete(p) and fp:
            return {"action": "MIGRATE", "target": target, "source": p.product_id, "preliminary": False,
                    "reasons": ["COMMERCIALLY_OBSOLETE", "ACTIVE_FOOTPRINT_MIGRATE_NOT_RETIRE"]}
        if obsolete(p):
            return {"action": "RETIRE", "target": None, "source": p.product_id, "preliminary": False,
                    "reasons": ["COMMERCIALLY_OBSOLETE", "NO_ACTIVE_FOOTPRINT"]}
        return {"action": "MIGRATE", "target": target, "source": p.product_id, "preliminary": False,
                "reasons": ["LEGACY_DEPENDENCY"]}
    return {"action": "SIMPLIFY", "target": None, "source": p.product_id, "preliminary": False,
            "reasons": [f"{c.primary_type}_SIGNAL"]}


def guardrail(score: int | None, dims: dict[str, dict], action: str | None, ctx: Context) -> tuple[str, list[str]]:
    st = ctx.settings
    mandatory = ["COMMERCIAL"] + (["TECHNICAL"] if tech_gate_applies(st.get("technicalEquivalenceGate", ""), action)
                                  else [])
    if any(dims[d]["status"] == "BLOCKED" for d in mandatory):
        return "BLOCKED", [f"{d} dimension is blocked" for d in mandatory if dims[d]["status"] == "BLOCKED"]
    if any(dims[d]["status"] == "NOT_ASSESSED" for d in mandatory):
        return "NOT_ASSESSED", [f"{d} dimension is not assessed" for d in mandatory
                                if dims[d]["status"] == "NOT_ASSESSED"]
    if score is None:
        return "NOT_ASSESSED", ["Too little evidence for an Opportunity Score"]
    rec, rev = st.get("recommendedThreshold", 80), st.get("reviewThreshold", 60)
    gaps = [f"{d} dimension is partial" for d in mandatory if dims[d]["status"] != "ASSESSED"]
    if score >= rec and not gaps:
        return "RECOMMENDED", [f"Score {score} ≥ {rec}"]
    if score >= rev:
        return "REVIEW_REQUIRED", [f"Score {score} ≥ {rev}"] + gaps + ([f"Score {score} < {rec}"] if score < rec else [])
    return "BELOW_REVIEW", [f"Score {score} < {rev}"]


def _name(ctx: Context, pid: str | None) -> str:
    if pid is None:
        return "—"
    p = ctx.products.get(pid)
    return p.name if p and p.name else pid


TEMPLATES = {
    "CONSOLIDATE": "Move {source} to {target}.",
    "MIGRATE": "Migrate the {source} base to {target}.",
    "STANDARDISE": "Standardise {source} onto the technical model of {target}.",
    "SIMPLIFY": "Simplify {source}.",
    "RETIRE": "Retire {source}: no active customers or services remain.",
    "INVESTIGATE": "Investigate {source}: the evidence does not support a decision yet.",
    "RETAIN": "Retain {source}.",
}
CONDITION_TEXT = {
    "PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL": "Preserve contracted price until renewal.",
    "MIGRATE_LEGACY_BILLING_DEPENDENCY": "Move the legacy billing dependency first.",
    "MIGRATE_ACTIVE_BASE": "Migrate active customers and services before withdrawal.",
}


def recommendation(c: CandidateInput, ctx: Context, prelim: dict[str, Any], guard: str) -> dict[str, Any]:
    action = prelim["action"]
    reasons = list(prelim["reasons"])
    hidden = False
    if guard == "BELOW_REVIEW":
        treat = ctx.settings.get("belowReviewTreatment", "NO_RECOMMENDATION")
        reasons.append("BELOW_REVIEW_THRESHOLD")
        if treat == "NO_RECOMMENDATION":
            action = None
        elif treat == "INVESTIGATE":
            action = "INVESTIGATE"
        else:
            hidden = True
    src = ctx.products.get(prelim["source"])
    conditions: list[str] = []
    if src is not None and action in ("CONSOLIDATE", "MIGRATE", "RETIRE", "STANDARDISE"):
        if (src.protected_contracts or 0) > 0:
            conditions.append("PRESERVE_CONTRACTED_PRICE_UNTIL_RENEWAL")
        if src.legacy_billing and action in ("CONSOLIDATE", "MIGRATE"):
            conditions.append("MIGRATE_LEGACY_BILLING_DEPENDENCY")
        if action == "MIGRATE" and footprint(src):
            conditions.append("MIGRATE_ACTIVE_BASE")
    text = None
    if action:
        template = TEMPLATES[action]
        if action == "SIMPLIFY" and prelim["target"]:
            template = "Rationalise the overlap: keep {target}, simplify {source}."
        text = template.format(source=_name(ctx, prelim["source"]), target=_name(ctx, prelim["target"]))
        if action in ("MIGRATE", "CONSOLIDATE", "STANDARDISE") and prelim["target"] is None:
            text = text.replace(" to —", " to a target to be chosen").replace(" of —", " of a chosen target")
        text = " ".join([text] + [CONDITION_TEXT[x] for x in conditions])
    return {"action": action, "targetProductId": prelim["target"], "sourceProductId": prelim["source"],
            "conditions": conditions, "reasonCodes": reasons, "preliminary": prelim["preliminary"],
            "text": text, "hidden": hidden, "guardrail": guard}


# ---------------------------------------------------------------------------
# Impact facts
# ---------------------------------------------------------------------------

def _money(amount: float, currency: str | None) -> str:
    sym = CURRENCY_SYMBOL.get(currency or "", "")
    if amount >= 1_000_000:
        v = f"{amount / 1_000_000:.1f}".rstrip("0").rstrip(".") + "m"
    elif amount >= 1_000:
        v = f"{amount / 1_000:.0f}k"
    else:
        v = f"{amount:.0f}"
    return f"{sym}{v}" if sym else f"{v} {currency or ''}".strip()


def _count(n: int) -> str:
    return f"{n / 1000:.0f}k" if n >= 10_000 else f"{n:,}"


def impact(c: CandidateInput, ctx: Context, source_id: str) -> dict[str, Any]:
    p = ctx.products[source_id]
    fm = fallout_multiple(p, ctx)
    rate = p.fallout / p.orders if p.fallout is not None and p.orders else None
    summary = []
    if p.mrr is not None:
        summary.append(_money(p.mrr, p.currency))
    if p.subscriptions is not None:
        summary.append(f"{_count(p.subscriptions)} subs")
    if not summary and fm is not None:
        summary.append(f"{fm:.1f}× fallout")
    ev = p.evidence
    return {
        "productId": source_id,
        "activeSubscriptions": p.subscriptions,
        "protectedPriceContracts": p.protected_contracts,
        "mrr": None if p.mrr is None else {"amount": p.mrr, "currency": p.currency, "period": "MONTH"},
        "billingSystems": len(p.billing_systems) if p.legacy_billing is not None else None,
        "billingSystemIds": sorted(p.billing_systems),
        "legacyBillingCode": p.legacy_billing,
        "activeServices": p.inventory_active,
        "falloutRate": None if rate is None else round(rate, 4),
        "familyFalloutRate": None if ctx.family_fallout.get(p.family) is None else round(ctx.family_fallout[p.family], 4),
        "falloutMultiple": None if fm is None else round(fm, 1),
        "summary": " / ".join(summary) if summary else "Impact not assessed",
        "evidenceIds": sorted(set(ev.get("customer", []) + ev.get("billing", []) + ev.get("order_billing", [])
                                  + ev.get("inventory", []))),
    }


# ---------------------------------------------------------------------------
# Priority (separate from the score)
# ---------------------------------------------------------------------------

def _band(index: int, bands: list[dict]) -> str:
    out = "LOW"
    for b in sorted(bands, key=lambda b: b["min"]):
        if index >= b["min"]:
            out = b["band"]
    return out


def sunset_days(ps: list[ProductFeatures], as_of: date) -> int | None:
    dates = [p.sunset or p.end_of_sale for p in ps if (p.sunset or p.end_of_sale)]
    return min((d - as_of).days for d in dates) if dates else None


def priority(c: CandidateInput, ctx: Context, rec: dict[str, Any], exposure_source: ProductFeatures,
             dependency_blocked: bool) -> dict[str, Any]:
    pol = ctx.priority
    rules = ctx.priority_rules()
    ps = [ctx.products[s] for s in c.subjects]
    burden = [x["signal"] for x in c.findings if x["type"] == "OPERATIONAL_BURDEN"]
    simpl = [x["signal"] / 10 for x in c.findings if x["type"] in ("COMPLEXITY", "OPERATIONAL_BURDEN")]
    business = _mean([REDUCTION.get(rec["action"]) if rec["action"] else None, _max(simpl)])
    exposure = None
    p = exposure_source
    if p.subscriptions is not None or p.mrr is not None or p.protected_contracts is not None:
        pops = [
            (p.subscriptions, [x.subscriptions for x in ctx.products.values() if x.subscriptions is not None]),
            (p.mrr, [x.mrr for x in ctx.products.values() if x.mrr is not None]),
            (p.protected_contracts, [x.protected_contracts for x in ctx.products.values()
                                     if x.protected_contracts is not None]),
        ]
        exposure = 100 * max(percentile(v, pop) for v, pop in pops if v is not None)
    days = sunset_days(ps, ctx.as_of)
    legacy = None
    if any(x.lifecycle is not None or x.sunset or x.end_of_sale for x in ps):
        if days is None:
            legacy = rules["noSunsetValue"]
        else:
            table = sorted(pol.get("sunsetUrgency", []) + rules["legacyCurve"], key=lambda r: r["withinDays"])
            legacy = next((r["value"] for r in table if days <= r["withinDays"]), 20)
    dep = None
    if any(x.legacy_billing is not None or x.workflows for x in ps):
        dep = 100 if dependency_blocked else 70 if any(x.legacy_billing for x in ps) else \
            40 if any(any(w.get("legacy") for w in x.workflows) for x in ps) else 0
    factors = {
        "businessImpact": business, "exposure": exposure,
        "operationalBurden": max(burden) / 10 if burden else None, "legacyUrgency": legacy,
        "regulatoryRisk": None,  # no regulated-service evidence in the canonical model yet
        "dependencyCriticality": dep, "strategicAlignment": 100 * strategic_alignment(c, ctx),
    }
    weights = pol["weights"]
    assessed = {k: v for k, v in factors.items() if v is not None}
    total_w = sum(weights[k] for k in assessed)
    coverage = total_w * 100 // sum(weights.values())
    index = round_half_up(sum(weights[k] * v for k, v in assessed.items()) / total_w) \
        if total_w and coverage >= rules["minAssessedWeight"] else None
    base = _band(index, pol["bands"]) if index is not None else None
    applied = []
    for o in pol.get("overrides", []):
        hit = False
        if o["kind"] == "SUNSET" and days is not None and days <= o.get("withinDays", 90):
            hit = True
        elif o["kind"] == "DEPENDENCY" and dep is not None and dep >= 70:
            hit = True
        elif o["kind"] == "CUSTOMER_IMPACT" and (p.subscriptions or 0) >= o.get("minSubscriptions", 10 ** 12):
            hit = True
        elif o["kind"] == "REGULATORY" and (factors["regulatoryRisk"] or 0) >= 75:
            hit = True
        if hit:
            applied.append({"kind": o["kind"], "minimumBand": o["minimumBand"]})
    levels = [BANDS.index(x) for x in ([base] if base else []) + [a["minimumBand"] for a in applied]]
    effective = BANDS[max(levels)] if levels else None
    return {"index": index, "base": base, "effective": effective, "coverage": coverage, "overrides": applied,
            "sunsetDays": days, "factors": {k: {"value": None if v is None else round_half_up(v),
                                                "weight": weights[k]} for k, v in factors.items()}}


# ---------------------------------------------------------------------------
# Blockers and status
# ---------------------------------------------------------------------------

@dataclass
class BlockerDraft:
    rule: str
    category: str  # EVIDENCE | VALIDATION | POLICY | DEPENDENCY | WORKFLOW
    consequence: str  # BLOCKED | WARNING_ONLY
    severity: str
    domain: str
    subject: str
    message: str
    owner_role: str
    due_date: date
    facts: dict[str, Any]

    @property
    def key(self) -> str:
        return f"{self.rule}|{self.facts.get('key', '')}"


def _severity(base: str, high_exposure: bool) -> str:
    i = SEVERITY.index(base)
    return SEVERITY[min(3, i + 1)] if high_exposure else base


def blockers(c: CandidateInput, ctx: Context, rec: dict[str, Any], dims: dict[str, dict]) -> list[BlockerDraft]:
    out: list[BlockerDraft] = []
    action = rec["action"]
    src = ctx.products.get(rec["sourceProductId"])
    tgt = ctx.products.get(rec["targetProductId"]) if rec["targetProductId"] else None
    threshold = next((o.get("minSubscriptions") for o in ctx.priority.get("overrides", [])
                      if o["kind"] == "CUSTOMER_IMPACT"), None)
    high_exposure = bool(src and threshold and (src.subscriptions or 0) >= threshold)
    subject = _name(ctx, rec["sourceProductId"])

    def add(rule, category, consequence, base, domain, message, facts):
        sev = _severity(base, high_exposure and consequence == "BLOCKED")
        out.append(BlockerDraft(rule, category, consequence, sev, domain, subject, message,
                                OWNER_BY_DOMAIN.get(domain, "portfolio_manager"),
                                ctx.as_of + timedelta(days=SLA_DAYS[sev]), facts))

    # EVIDENCE: domains the recommended action needs (readiness policy, rule 6) + the technical gate
    if action and action != "INVESTIGATE":
        needed = list(ctx.dispositions.get(action, []))
        if tech_gate_applies(ctx.settings.get("technicalEquivalenceGate", ""), action):
            needed += [d for d in ctx.rules()["TECHNICAL"]["mandatory"] if d not in needed]
        for d in needed:
            lacking = [s for s in c.subjects if d not in ctx.coverage.get(s, set())]
            if lacking:
                imp = ctx.domain_importance.get(d, "HIGH")
                add("EVIDENCE_REQUIRED", "EVIDENCE", "BLOCKED", "HIGH" if imp == "CRITICAL" else "MEDIUM",
                    EVIDENCE_DOMAIN_TO_BLOCKER_DOMAIN.get(d, "DATA"),
                    f"{ctx.domain_labels.get(d, d)} evidence missing for {action.lower()}",
                    {"key": d, "domain": d, "products": lacking, "action": action})
    # EVIDENCE: stale domains
    for d in sorted({x for dim in dims.values() for x in dim["staleDomains"]}):
        treat = ctx.settings.get("staleEvidenceTreatment", "REQUIRE_REFRESH")
        if treat == "ALLOW_WITH_WARNING":
            continue
        add("EVIDENCE_STALE", "EVIDENCE", "BLOCKED" if treat == "BLOCK" else "WARNING_ONLY",
            "HIGH" if treat == "BLOCK" else "LOW", EVIDENCE_DOMAIN_TO_BLOCKER_DOMAIN.get(d, "DATA"),
            f"{ctx.domain_labels.get(d, d)} evidence is older than its freshness limit", {"key": d, "domain": d})
    # VALIDATION: unresolved material conflicts on the candidate's products
    conflicts = sorted({f for s in c.subjects for f in ctx.conflicted.get(s, [])})
    if conflicts:
        block = ctx.settings.get("criticalConflictTreatment", "BLOCK") == "BLOCK"
        add("MATERIAL_CONFLICT", "VALIDATION", "BLOCKED" if block else "WARNING_ONLY", "HIGH" if block else "MEDIUM",
            "DATA", f"Unresolved source conflict on {', '.join(conflicts[:3])}", {"key": ",".join(conflicts),
                                                                                "fields": conflicts})
    # DEPENDENCY: no valid migration target for provisioning or billing
    if src is not None and action in ("MIGRATE", "CONSOLIDATE"):
        bad = [w["id"] for w in src.workflows if w.get("compatibility") == "INCOMPATIBLE"]
        if bad:
            add("PROVISIONING_INCOMPATIBLE", "DEPENDENCY", "BLOCKED", "HIGH", "PROVISIONING",
                f"Provisioning workflow {', '.join(bad)} is incompatible with the target platform",
                {"key": ",".join(bad), "workflows": bad})
        if src.legacy_billing and (tgt is None or not tgt.billing_systems or tgt.legacy_billing is None):
            add("BILLING_TARGET_MISSING", "DEPENDENCY", "BLOCKED", "HIGH", "BILLING",
                "Legacy billing dependency has no valid target billing system", {"key": rec["targetProductId"] or "-",
                                                                                 "target": rec["targetProductId"]})
    return out


def candidate_status(active_blockers: list[dict[str, Any]], guard: str, decision_state: str | None = None) -> str:
    """Precedence BLOCKED > EVIDENCE_GAP > APPROVAL > DECISION > REVIEW; APPROVED once approved."""
    blocking = [b for b in active_blockers if b["consequence"] == "BLOCKED"]
    if decision_state == "APPROVED":
        return "APPROVED"
    if any(b["category"] != "EVIDENCE" for b in blocking):
        return "BLOCKED"
    if any(b["category"] == "EVIDENCE" for b in blocking) or guard == "NOT_ASSESSED":
        return "EVIDENCE_GAP"
    if decision_state in ("SUBMITTED", "IN_APPROVAL"):
        return "APPROVAL"
    if decision_state == "DRAFT":
        return "DECISION"
    return "REVIEW"


# ---------------------------------------------------------------------------
# Whole candidate
# ---------------------------------------------------------------------------

def assess(c: CandidateInput, ctx: Context) -> dict[str, Any]:
    weights = ctx.scoring["weights"]
    # the product being changed (source); for pairs the deterministic target selection decides it
    if c.primary_type in PAIR_TYPES and len(c.subjects) == 2:
        _, source_id, _ = select_target(c, ctx)
    else:
        source_id = c.subjects[0]
    source = ctx.products[source_id]
    inputs = score_inputs(c, ctx, source)
    opp = opportunity_score(inputs, weights, ctx.rules()["minAssessedWeight"])
    dims = {d: dimension(d, c, ctx, source) for d in DIMENSIONS}
    prelim = preliminary_action(c, ctx, dims)
    guard, guard_reasons = guardrail(opp["score"], dims, prelim["action"], ctx)
    rec = recommendation(c, ctx, prelim, guard)
    drafts = blockers(c, ctx, rec, dims)
    dep_blocked = any(b.category == "DEPENDENCY" and b.consequence == "BLOCKED" for b in drafts)
    prio = priority(c, ctx, rec, ctx.products[rec["sourceProductId"]], dep_blocked)
    similarity = None
    if c.primary_type in PAIR_TYPES:
        sig = [x["signal"] for x in c.findings if x["type"] == c.primary_type]
        similarity = max(sig) if sig else None
    return {
        "similarity": similarity,
        "opportunity": opp,
        "dimensions": dims,
        "guardrail": guard,
        "guardrailReasons": guard_reasons,
        "recommendation": rec,
        "impact": impact(c, ctx, rec["sourceProductId"]),
        "priority": prio,
        "blockers": drafts,
    }
