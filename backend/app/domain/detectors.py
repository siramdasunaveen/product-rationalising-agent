"""Detectors (BUILD_PLAN step 10). Pure and deterministic: findings, not candidates.

Each detector reads product features built from one approved snapshot and the pinned detector pack (weights in
percent, thresholds per mille). A factor without evidence is ``None`` (NOT_ASSESSED): it is left out and the other
weights are re-normalised; it never counts as 0 (CLAUDE.md rule 1). A pair finding needs at least
``MIN_FACTOR_COVERAGE`` percent of the factor weight to be assessed. Family P95 normalisation needs a baseline of at
least ``MIN_BASELINE`` products in the scope family, otherwise the detector is not assessed for that family.
No LLM computes anything here.
"""

import hashlib
import math
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date
from typing import Any

MIN_FACTOR_COVERAGE = 60  # percent of factor weight that must be assessed for a similarity finding
MIN_BASELINE = 10  # products with a value in the scope family for P95 normalisation (below: P95 is just the max)
LEGACY_LIFECYCLE = {"END_OF_SALE", "END_OF_SUPPORT", "SUNSET", "RETIRED"}
_VERSION_TOKEN = re.compile(r"^(v\d+|version\d*|\d+(\.\d+)?)$")


@dataclass
class ProductFeatures:
    product_id: str
    name: str = ""
    family: str | None = None  # scope family code (the selected family node the product belongs to)
    status: str | None = None
    segment: str | None = None
    market: str | None = None
    channel: str | None = None
    parent: str | None = None
    offers: set[str] = field(default_factory=set)
    cpq_options: set[str] = field(default_factory=set)  # configuration/eligibility options
    cpq_rule_count: int | None = None
    prices: dict[str, float] = field(default_factory=dict)  # currency -> list price
    lifecycle: str | None = None
    end_of_sale: date | None = None
    sunset: date | None = None
    replacement: str | None = None
    services: set[str] = field(default_factory=set)
    service_types: set[str] = field(default_factory=set)
    resources: set[str] = field(default_factory=set)
    workflows: list[dict[str, Any]] = field(default_factory=list)  # {legacy, compatibility}
    inventory_active: int | None = None
    inventory_variants: int | None = None
    inventory_legacy: bool | None = None
    orders: int | None = None
    fallout: int | None = None
    manual_touch: int | None = None
    incidents: int | None = None
    provisioning_exceptions: int | None = None
    handling_minutes: float | None = None
    subscriptions: int | None = None
    protected_contracts: int | None = None
    mrr: float | None = None
    currency: str | None = None
    billing_systems: set[str] = field(default_factory=set)
    legacy_billing: bool | None = None
    evidence: dict[str, list[str]] = field(default_factory=dict)  # group -> evidence ids


@dataclass
class Finding:
    key: str
    detector_type: str
    detector_version: str
    subjects: list[str]
    signal: int  # per mille
    threshold: int
    band: str  # STRONG | PROBABLE | MATCH
    factors: dict[str, dict[str, Any]]  # name -> {value (per mille or None), weight}
    coverage: int  # percent of weight assessed
    evidence_ids: list[str]
    notes: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {"key": self.key, "type": self.detector_type, "version": self.detector_version,
                "subjects": self.subjects, "signal": self.signal, "threshold": self.threshold, "band": self.band,
                "factors": self.factors, "coverage": self.coverage, "evidence_ids": self.evidence_ids,
                "notes": self.notes}


def finding_key(detector_type: str, subjects: list[str]) -> str:
    return hashlib.sha256((detector_type + "|" + "|".join(sorted(subjects))).encode()).hexdigest()[:16]


def _mille(x: float | None) -> int | None:
    return None if x is None else max(0, min(1000, round(x * 1000)))


def tokens(name: str) -> set[str]:
    """Name tokens without version markers: 'Mobile Data 15GB V1' -> {'mobile', 'data', '15gb'}."""
    return {t for t in re.split(r"[^a-z0-9.]+", name.lower()) if t and not _VERSION_TOKEN.match(t)}


def jaccard(a: set, b: set) -> float | None:
    if not a or not b:
        return None
    return len(a & b) / len(a | b)


def _eq(a: str | None, b: str | None) -> float | None:
    if not a or not b:
        return None
    return 1.0 if a.strip().lower() == b.strip().lower() else 0.0


def _avg(values: list[float | None]) -> float | None:
    vs = [v for v in values if v is not None]
    return sum(vs) / len(vs) if vs else None


def weighted(factors: dict[str, float | None], weights: dict[str, int]) -> tuple[float | None, int]:
    assessed = {k: v for k, v in factors.items() if v is not None}
    total = sum(weights[k] for k in assessed)
    coverage = total * 100 // sum(weights.values()) if weights else 0
    if not total:
        return None, coverage
    return sum(weights[k] * v for k, v in assessed.items()) / total, coverage


def _replaces(a: ProductFeatures, b: ProductFeatures) -> bool:
    return a.replacement == b.product_id or b.replacement == a.product_id


def _evidence(products: list[ProductFeatures], groups: tuple[str, ...]) -> list[str]:
    out: set[str] = set()
    for p in products:
        for g in groups:
            out.update(p.evidence.get(g, []))
    return sorted(out)


# ---------------------------------------------------------------------------
# Pair detectors: DUPLICATE and OVERLAP
# ---------------------------------------------------------------------------

def duplicate_factors(a: ProductFeatures, b: ProductFeatures) -> dict[str, float | None]:
    price = None
    common = set(a.prices) & set(b.prices)
    if common:
        cur = sorted(common)[0]
        x, y = a.prices[cur], b.prices[cur]
        price = 1.0 if max(x, y) == 0 else 1 - abs(x - y) / max(x, y)
    elif a.prices and b.prices:
        price = 0.0
    lifecycle = None
    if a.lifecycle and b.lifecycle:
        lifecycle = 1.0 if a.lifecycle == b.lifecycle or _replaces(a, b) else 0.5
    return {
        "service": jaccard(tokens(a.name), tokens(b.name)),
        "cpq": jaccard(a.cpq_options, b.cpq_options),
        "eligibility": _avg([_eq(a.segment, b.segment), _eq(a.market, b.market), _eq(a.channel, b.channel)]),
        "pricingStructure": price,
        "technical": jaccard(a.services | a.resources, b.services | b.resources) if a.services and b.services else None,
        "lifecycle": lifecycle,
    }


def overlap_factors(a: ProductFeatures, b: ProductFeatures) -> dict[str, float | None]:
    capability = None
    if a.services and b.services:
        capability = 1.0 if a.services & b.services else (0.5 if a.service_types & b.service_types else 0.0)
    substitution = None
    if a.lifecycle or b.lifecycle:
        substitution = 1.0 if _replaces(a, b) or a.parent == b.product_id or b.parent == a.product_id else 0.0
    return {
        "capability": capability,
        "segment": _eq(a.segment, b.segment),
        "channelMarket": _avg([_eq(a.channel, b.channel), _eq(a.market, b.market)]),
        "useCase": jaccard(tokens(a.name), tokens(b.name)),
        "substitution": substitution,
    }


def _factor_out(values: dict[str, float | None], weights: dict[str, int]) -> dict[str, dict[str, Any]]:
    return {k: {"value": _mille(values[k]), "weight": weights[k]} for k in weights}


def pair_findings(products: list[ProductFeatures], pack: dict) -> list[Finding]:
    det = {d["type"]: d for d in pack["detectors"] if d["enabled"]}
    out: list[Finding] = []
    by_family: dict[str | None, list[ProductFeatures]] = defaultdict(list)
    for p in products:
        by_family[p.family].append(p)
    for fam in sorted(by_family, key=lambda f: f or ""):
        group = sorted(by_family[fam], key=lambda p: p.product_id)
        for i, a in enumerate(group):
            for b in group[i + 1:]:
                subjects = [a.product_id, b.product_id]
                dup_sim = None
                if "DUPLICATE" in det:
                    d = det["DUPLICATE"]
                    vals = duplicate_factors(a, b)
                    sim, cov = weighted(vals, d["factors"])
                    dup_sim = sim if cov >= MIN_FACTOR_COVERAGE else None
                    th = d["thresholds"]
                    if dup_sim is not None and _mille(dup_sim) >= th["probable"]:
                        band = "STRONG" if _mille(dup_sim) >= th["strong"] else "PROBABLE"
                        out.append(Finding(finding_key("DUPLICATE", subjects), "DUPLICATE", d["version"], subjects,
                                           _mille(dup_sim), th["probable"], band, _factor_out(vals, d["factors"]),
                                           cov, _evidence([a, b], ("product", "cpq", "price", "lifecycle",
                                                                   "technical"))))
                if "OVERLAP" in det:
                    d = det["OVERLAP"]
                    vals = overlap_factors(a, b)
                    score, cov = weighted(vals, d["factors"])
                    probable = det.get("DUPLICATE", {}).get("thresholds", {}).get("probable", 1001)
                    if score is not None and cov >= MIN_FACTOR_COVERAGE and _mille(score) >= d["thresholds"]["match"] \
                            and (dup_sim is None or _mille(dup_sim) < probable):
                        out.append(Finding(finding_key("OVERLAP", subjects), "OVERLAP", d["version"], subjects,
                                           _mille(score), d["thresholds"]["match"], "MATCH",
                                           _factor_out(vals, d["factors"]), cov,
                                           _evidence([a, b], ("product", "technical", "lifecycle"))))
    return out


# ---------------------------------------------------------------------------
# Single-product detectors: LEGACY, COMPLEXITY, OPERATIONAL_BURDEN
# ---------------------------------------------------------------------------

def legacy_findings(products: list[ProductFeatures], pack: dict, today: date) -> list[Finding]:
    d = next((x for x in pack["detectors"] if x["type"] == "LEGACY" and x["enabled"]), None)
    if d is None:
        return []
    horizon = d.get("rules", {}).get("sunsetHorizonDays", 365)
    out = []
    for p in sorted(products, key=lambda x: x.product_id):
        eos = None
        if p.lifecycle is not None or p.end_of_sale is not None:
            eos = (p.lifecycle in LEGACY_LIFECYCLE) or (p.end_of_sale is not None and p.end_of_sale <= today)
        dep = None
        if p.legacy_billing is not None or p.sunset is not None:
            dep = bool(p.legacy_billing) or (p.sunset is not None and (p.sunset - today).days <= horizon)
        wf = any(w.get("legacy") for w in p.workflows) if p.workflows else None
        repl = None
        if p.replacement is not None and p.inventory_legacy is not None:
            repl = bool(p.inventory_legacy)
        conditions = {"endOfSale": eos, "criticalDependencySunset": dep, "legacyProvisioningWorkflow": wf,
                      "replacementWithLegacyArchitecture": repl}
        true = [k for k, v in conditions.items() if v]
        if not true:
            continue
        signal = min(1000, 600 + 100 * len(true))
        notes = []
        if p.inventory_active:
            notes.append(f"Installed base {p.inventory_active} active services: migrate, never retire silently")
        out.append(Finding(finding_key("LEGACY", [p.product_id]), "LEGACY", d["version"], [p.product_id], signal, 700,
                           "MATCH", {k: {"value": None if v is None else (1000 if v else 0), "weight": None}
                                     for k, v in conditions.items()}, 100,
                           _evidence([p], ("product", "lifecycle", "provisioning", "inventory", "billing")), notes))
    return out


def p95(values: list[float]) -> float:
    s = sorted(values)
    return s[max(0, math.ceil(0.95 * len(s)) - 1)]


def _normalised(products: list[ProductFeatures], raw: dict[str, dict[str, float | None]],
                factors: tuple[str, ...]) -> tuple[dict[str, dict[str, float | None]], dict[str, list[str]]]:
    """Per family: value / family P95 (capped 1); None when the baseline has fewer than MIN_BASELINE values."""
    out: dict[str, dict[str, float | None]] = {p.product_id: {} for p in products}
    notes: dict[str, list[str]] = defaultdict(list)
    fam: dict[str | None, list[ProductFeatures]] = defaultdict(list)
    for p in products:
        fam[p.family].append(p)
    for f, members in fam.items():
        for factor in factors:
            vals = [raw[p.product_id][factor] for p in members if raw[p.product_id][factor] is not None]
            base = p95(vals) if len(vals) >= MIN_BASELINE else None
            for p in members:
                v = raw[p.product_id][factor]
                if v is None or base is None:
                    out[p.product_id][factor] = None
                    if v is not None:
                        notes[p.product_id].append(f"{factor}: family baseline below {MIN_BASELINE} products")
                else:
                    out[p.product_id][factor] = min(1.0, v / base) if base else (1.0 if v > 0 else 0.0)
    return out, notes


def _rate(num: int | None, den: int | None) -> float | None:
    if num is None or not den:
        return None
    return num / den


def _bundle_depth(p: ProductFeatures, by_id: dict[str, ProductFeatures]) -> int:
    depth, cur, seen = 0, p, set()
    while cur.parent and cur.parent in by_id and cur.parent not in seen:
        seen.add(cur.parent)
        depth += 1
        cur = by_id[cur.parent]
    return depth + (1 if p.offers else 0)


def single_findings(products: list[ProductFeatures], pack: dict) -> list[Finding]:
    by_id = {p.product_id: p for p in products}
    out: list[Finding] = []
    specs = {
        "COMPLEXITY": (("variant", "rule", "bundleDepth", "attributeEntropy", "manualException"),
                       lambda p: {"variant": p.inventory_variants, "rule": p.cpq_rule_count,
                                  "bundleDepth": _bundle_depth(p, by_id) if p.parent or p.offers else None,
                                  "attributeEntropy": len(p.cpq_options) if p.cpq_options else None,
                                  "manualException": _rate(p.manual_touch, p.orders)},
                       ("cpq", "inventory", "order_billing")),
        "OPERATIONAL_BURDEN": (("fallout", "manualTouch", "incident", "provisioningException", "handling"),
                               lambda p: {"fallout": _rate(p.fallout, p.orders),
                                          "manualTouch": _rate(p.manual_touch, p.orders),
                                          "incident": _rate(p.incidents, p.orders),
                                          "provisioningException": _rate(p.provisioning_exceptions, p.orders),
                                          "handling": p.handling_minutes},
                               ("order_billing",)),
    }
    for dtype, (factors, extract, groups) in specs.items():
        d = next((x for x in pack["detectors"] if x["type"] == dtype and x["enabled"]), None)
        if d is None:
            continue
        raw = {p.product_id: extract(p) for p in products}
        norm, notes = _normalised(products, raw, factors)
        for p in sorted(products, key=lambda x: x.product_id):
            score, cov = weighted(norm[p.product_id], d["factors"])
            limits = d.get("hardLimits", {})
            hard = [k for k, lim in (("variants", p.inventory_variants), ("rules", p.cpq_rule_count))
                    if lim is not None and k in limits and lim > limits[k]]
            if score is None and not hard:
                continue
            if cov < MIN_FACTOR_COVERAGE and not hard:
                continue
            m = _mille(score) or 0
            if m >= d["thresholds"]["match"] or hard:
                out.append(Finding(finding_key(dtype, [p.product_id]), dtype, d["version"], [p.product_id], m,
                                   d["thresholds"]["match"], "MATCH", _factor_out(norm[p.product_id], d["factors"]),
                                   cov, _evidence([p], groups),
                                   notes[p.product_id] + [f"Hard limit exceeded: {h}" for h in hard]))
    return out


def run_detectors(products: list[ProductFeatures], pack: dict, today: date) -> tuple[list[Finding], dict[str, Any]]:
    findings = pair_findings(products, pack) + legacy_findings(products, pack, today) + single_findings(products, pack)
    findings.sort(key=lambda f: (f.detector_type, f.subjects))
    summary = {"rawFindings": len(findings)}
    for t in ("DUPLICATE", "OVERLAP", "LEGACY", "COMPLEXITY", "OPERATIONAL_BURDEN"):
        summary[t.lower()] = sum(1 for f in findings if f.detector_type == t)
    return findings, summary
