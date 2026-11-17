"""Evidence readiness (BUILD_PLAN step 7, screen 11). Pure and deterministic: no I/O, no AI.

From an evidence snapshot (or a preview of the latest reconciliation) and the published readiness policy, compute
per evidence domain: coverage of the run's products, status (AVAILABLE | PARTIAL | NOT_ASSESSED | BLOCKED, CLAUDE.md
rule 2), stale/conflicted flags, the consequence of missing evidence and the required action; then the KPIs, which
assessments are available, and which dispositions could be decided from this evidence (rule 6).

Missing evidence is never turned into a low score (rule 1): a domain without data is NOT_ASSESSED (shown as
"Missing"), and the assessments that depend on it are NOT_ASSESSED or BLOCKED, never scored.
"""

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from app.domain.artifacts import canonical_json
from app.domain.common import FieldError


class Status(StrEnum):
    AVAILABLE = "AVAILABLE"
    PARTIAL = "PARTIAL"
    NOT_ASSESSED = "NOT_ASSESSED"
    BLOCKED = "BLOCKED"


class AssessmentStatus(StrEnum):
    AVAILABLE = "AVAILABLE"
    LIMITED = "LIMITED"
    NOT_ASSESSED = "NOT_ASSESSED"
    BLOCKED = "BLOCKED"


DISPOSITIONS = ("RETAIN", "SIMPLIFY", "CONSOLIDATE", "STANDARDISE", "MIGRATE", "RETIRE", "INVESTIGATE")
IMPORTANCE = ("CRITICAL", "HIGH", "MEDIUM", "LOW")
MEASURE_TYPES = ("SUBJECT_FIELDS", "RECORD_REFERENCING", "SUBJECT_LINK", "SUBJECT_OR_SERVICE_LINK", "SERVICE_RECORD")
STATUS_LABEL = {Status.AVAILABLE: "Available", Status.PARTIAL: "Partial", Status.NOT_ASSESSED: "Missing",
                Status.BLOCKED: "Blocked"}

# ---------------------------------------------------------------------------
# Policy content (registry kind READINESS_POLICY)
# ---------------------------------------------------------------------------

_NAME = {"type": "string", "pattern": r"^[A-Z][A-Za-z0-9]*$", "maxLength": 64}
_FIELD = {"type": "string", "pattern": r"^[a-z][A-Za-z0-9]*$", "maxLength": 64}
_REL = {"type": "string", "pattern": r"^[A-Z][A-Z0-9_]*$", "maxLength": 64}
_DOMAIN = {"type": "string", "pattern": r"^[A-Z][A-Z0-9_]*$", "maxLength": 64}
_TEXT = {"type": "object", "required": ["consequence", "action"], "additionalProperties": False,
         "properties": {"consequence": {"type": "string", "maxLength": 300},
                        "action": {"type": "string", "maxLength": 300}}}

READINESS_POLICY_JSON = {
    "type": "object",
    "required": ["canonicalSchema", "subject", "thresholds", "weights", "domains", "assessments", "dispositions"],
    "additionalProperties": False,
    "properties": {
        "description": {"type": "string", "maxLength": 2000},
        "canonicalSchema": {"type": "string", "maxLength": 64},
        "subject": {"type": "object", "required": ["entity"], "additionalProperties": False,
                    "properties": {"entity": _NAME}},
        "thresholds": {"type": "object", "required": ["available"], "additionalProperties": False,
                       "properties": {"available": {"type": "integer", "minimum": 1, "maximum": 100}}},
        "weights": {"type": "object", "additionalProperties": False,
                    "properties": {k: {"type": "integer", "minimum": 0, "maximum": 10} for k in IMPORTANCE},
                    "required": list(IMPORTANCE)},
        "staleBlocks": {"type": "array", "items": {"enum": list(IMPORTANCE)}, "uniqueItems": True},
        "domains": {
            "type": "array", "minItems": 1, "maxItems": 30,
            "items": {
                "type": "object", "required": ["domain", "importance", "measures", "text"],
                "additionalProperties": False,
                "properties": {
                    "domain": _DOMAIN,
                    "importance": {"enum": list(IMPORTANCE)},
                    "importanceLabel": {"type": "string", "maxLength": 80},
                    "measures": {"type": "array", "minItems": 1, "maxItems": 5, "items": {
                        "type": "object", "required": ["type"], "additionalProperties": False,
                        "properties": {"type": {"enum": list(MEASURE_TYPES)},
                                       "fields": {"type": "array", "items": _FIELD, "minItems": 1, "maxItems": 20},
                                       "entity": _NAME, "field": _FIELD, "relationship": _REL, "via": _REL}}},
                    "text": {"type": "object", "required": [s.value for s in Status], "additionalProperties": False,
                             "properties": {s.value: _TEXT for s in Status}},
                },
            },
        },
        "assessments": {
            "type": "array", "maxItems": 30,
            "items": {"type": "object", "required": ["key", "label", "requires", "whenMissing"],
                      "additionalProperties": False,
                      "properties": {"key": _REL, "label": {"type": "string", "maxLength": 120},
                                     "requires": {"type": "array", "items": _DOMAIN, "minItems": 1},
                                     "improvedBy": {"type": "array", "items": _DOMAIN},
                                     "whenMissing": {"enum": ["NOT_ASSESSED", "BLOCKED"]}}},
        },
        "dispositions": {"type": "object", "additionalProperties": False,
                         "properties": {d: {"type": "array", "items": _DOMAIN, "uniqueItems": True}
                                        for d in DISPOSITIONS},
                         "required": list(DISPOSITIONS)},
    },
}

MEASURE_PARAMS = {
    "SUBJECT_FIELDS": ("fields",),
    "RECORD_REFERENCING": ("entity", "field"),
    "SUBJECT_LINK": ("relationship",),
    "SUBJECT_OR_SERVICE_LINK": ("relationship", "via"),
    "SERVICE_RECORD": ("entity", "field", "via"),
}


def check_readiness_policy(content: dict) -> list[FieldError]:
    from app.domain.evidence import EvidenceDomain  # local import keeps domain modules free of cycles

    known = {d.value for d in EvidenceDomain}
    errors: list[FieldError] = []
    seen: set[str] = set()
    for i, d in enumerate(content["domains"]):
        if d["domain"] not in known:
            errors.append(FieldError(f"domains[{i}].domain", f"Unknown evidence domain {d['domain']}"))
        if d["domain"] in seen:
            errors.append(FieldError(f"domains[{i}].domain", f"{d['domain']} is listed twice"))
        seen.add(d["domain"])
        for j, m in enumerate(d["measures"]):
            missing = [p for p in MEASURE_PARAMS[m["type"]] if p not in m]
            if missing:
                errors.append(FieldError(f"domains[{i}].measures[{j}]", f"{m['type']} needs {', '.join(missing)}"))
    for i, a in enumerate(content["assessments"]):
        for d in a["requires"] + a.get("improvedBy", []):
            if d not in known:
                errors.append(FieldError(f"assessments[{i}]", f"Unknown evidence domain {d}"))
    for disp, domains in content["dispositions"].items():
        for d in domains:
            if d not in known:
                errors.append(FieldError(f"dispositions.{disp}", f"Unknown evidence domain {d}"))
    return errors


def check_readiness_policy_against(content: dict, canonical: dict) -> list[FieldError]:
    entities = {e["name"]: {f["name"] for f in e["fields"]} for e in canonical.get("entities", [])}
    rels = {r["name"] for r in canonical.get("relationships", [])}
    errors: list[FieldError] = []
    subject = content["subject"]["entity"]
    if subject not in entities:
        errors.append(FieldError("subject.entity", f"Entity {subject} is not in the schema"))
    for i, d in enumerate(content["domains"]):
        for j, m in enumerate(d["measures"]):
            path = f"domains[{i}].measures[{j}]"
            for f in m.get("fields", []):
                if f not in entities.get(subject, set()):
                    errors.append(FieldError(path, f"{subject}.{f} is not in the schema"))
            if "entity" in m and m.get("field") not in entities.get(m["entity"], set()):
                errors.append(FieldError(path, f"{m['entity']}.{m.get('field')} is not in the schema"))
            for r in (m.get("relationship"), m.get("via")):
                if r and r not in rels:
                    errors.append(FieldError(path, f"Relationship {r} is not in the schema"))
    return errors


# ---------------------------------------------------------------------------
# Evidence input
# ---------------------------------------------------------------------------

@dataclass
class Evidence:
    """Records keyed by (entity, key JSON), conflicted fields, relationship edges, stale domains."""

    records: dict[tuple[str, str], dict[str, Any]] = field(default_factory=dict)
    conflicted: set[tuple[str, str, str]] = field(default_factory=set)  # (entity, key, field)
    edges: list[tuple[str, str, str, str, str]] = field(default_factory=list)  # name, from_e, from_k, to_e, to_k
    stale_domains: set[str] = field(default_factory=set)


def key_value(key: str) -> Any:
    """'["PRD-1"]' -> 'PRD-1' (single-field keys), else the parsed list."""
    parts = json.loads(key)
    return parts[0] if len(parts) == 1 else parts


class _Index:
    def __init__(self, ev: Evidence) -> None:
        self.ev = ev
        self.out_edges: dict[tuple[str, str, str], list[tuple[str, str]]] = {}
        for name, fe, fk, te, tk in ev.edges:
            self.out_edges.setdefault((name, fe, fk), []).append((te, tk))
        self.referenced: dict[tuple[str, str], set[Any]] = {}

    def refs(self, entity: str, field_name: str) -> set[Any]:
        k = (entity, field_name)
        if k not in self.referenced:
            self.referenced[k] = {v.get(field_name) for (e, _), v in self.ev.records.items()
                                  if e == entity and v.get(field_name) is not None}
        return self.referenced[k]

    def linked(self, rel: str, entity: str, key: str) -> list[tuple[str, str]]:
        return self.out_edges.get((rel, entity, key), [])


def _covered(measure: dict, subject: str, key: str, idx: _Index) -> bool:
    t = measure["type"]
    ev = idx.ev
    if t == "SUBJECT_FIELDS":
        values = ev.records.get((subject, key), {})
        return all(values.get(f) is not None and (subject, key, f) not in ev.conflicted for f in measure["fields"])
    if t == "RECORD_REFERENCING":
        return key_value(key) in idx.refs(measure["entity"], measure["field"])
    if t == "SUBJECT_LINK":
        return bool(idx.linked(measure["relationship"], subject, key))
    if t == "SUBJECT_OR_SERVICE_LINK":
        if idx.linked(measure["relationship"], subject, key):
            return True
        return any(idx.linked(measure["relationship"], e, k) for e, k in idx.linked(measure["via"], subject, key))
    if t == "SERVICE_RECORD":
        refs = idx.refs(measure["entity"], measure["field"])
        return any(key_value(k) in refs for _, k in idx.linked(measure["via"], subject, key))
    raise ValueError(f"unknown measure {t}")


# ---------------------------------------------------------------------------
# Computation
# ---------------------------------------------------------------------------

def _fill(text: str, values: dict[str, Any]) -> str:
    out = text
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def domain_entities(domain: str) -> tuple[str, ...]:
    from app.domain.evidence import DOMAINS, EvidenceDomain

    try:
        return DOMAINS[EvidenceDomain(domain)].canonical_entities
    except ValueError:
        return ()


def domain_label(domain: str) -> str:
    from app.domain.evidence import DOMAINS, EvidenceDomain

    try:
        return DOMAINS[EvidenceDomain(domain)].label
    except ValueError:
        return domain


def compute(policy: dict, evidence: Evidence, selected: list[str]) -> dict[str, Any]:
    subject = policy["subject"]["entity"]
    subjects = sorted(k for (e, k) in evidence.records if e == subject)
    idx = _Index(evidence)
    threshold = policy["thresholds"]["available"]
    stale_blocks = set(policy.get("staleBlocks", []))
    selected_set = set(selected)

    domains: list[dict[str, Any]] = []
    status_of: dict[str, Status] = {}
    for d in policy["domains"]:
        name = d["domain"]
        covered = sum(1 for k in subjects if all(_covered(m, subject, k, idx) for m in d["measures"]))
        total = len(subjects)
        coverage = (100 * covered) // total if total else 0
        has_data = covered > 0
        if coverage >= threshold:
            status = Status.AVAILABLE
        elif has_data:
            status = Status.PARTIAL
        else:
            status = Status.NOT_ASSESSED
        stale = name in evidence.stale_domains
        if stale and has_data and d["importance"] in stale_blocks:
            status = Status.BLOCKED
        conflicted = sum(1 for (e, _, _) in evidence.conflicted if e in domain_entities(name))
        text = d["text"][status.value]
        values = {"coverage": coverage, "covered": covered, "total": total, "missing": total - covered}
        status_of[name] = status if name in selected_set else Status.NOT_ASSESSED
        domains.append({
            "domain": name, "label": domain_label(name), "selected": name in selected_set, "importance": d["importance"],
            "importance_label": d.get("importanceLabel") or d["importance"].title(),
            "status": status.value, "status_label": STATUS_LABEL[status], "coverage": coverage,
            "covered": covered, "total": total, "stale": stale, "conflicted_facts": conflicted,
            "consequence": _fill(text["consequence"], values), "action": _fill(text["action"], values),
        })

    weights = policy["weights"]
    in_scope = [x for x in domains if x["selected"]]
    weight_sum = sum(weights[x["importance"]] for x in in_scope)
    overall = round(sum(weights[x["importance"]] * x["coverage"] for x in in_scope) / weight_sum) if weight_sum else 0

    assessments = []
    for a in policy["assessments"]:
        req = [status_of.get(r, Status.NOT_ASSESSED) for r in a["requires"]]
        better = [status_of.get(r, Status.NOT_ASSESSED) for r in a.get("improvedBy", [])]
        if Status.BLOCKED in req:
            st = AssessmentStatus.BLOCKED
        elif Status.NOT_ASSESSED in req:
            st = AssessmentStatus(a["whenMissing"])
        elif Status.PARTIAL in req or any(s is not Status.AVAILABLE for s in better):
            st = AssessmentStatus.LIMITED
        else:
            st = AssessmentStatus.AVAILABLE
        limiting = [r for r in a["requires"] + a.get("improvedBy", [])
                    if status_of.get(r, Status.NOT_ASSESSED) is not Status.AVAILABLE]
        assessments.append({"key": a["key"], "label": a["label"], "status": st.value, "limited_by": limiting})

    dispositions = []
    for disp in DISPOSITIONS:
        needed = policy["dispositions"][disp]
        missing = [{"domain": d, "status": status_of.get(d, Status.NOT_ASSESSED).value} for d in needed
                   if status_of.get(d, Status.NOT_ASSESSED) is not Status.AVAILABLE]
        dispositions.append({"disposition": disp, "requires": needed, "ready": not missing, "missing": missing})

    gating = {d for disp in DISPOSITIONS if disp != "INVESTIGATE" for d in policy["dispositions"][disp]}
    kpis = {
        "overall_completeness": overall,
        "domains_available": sum(1 for x in in_scope if x["status"] == Status.AVAILABLE),
        "domains_selected": len(in_scope),
        "assessment_limitations": sum(1 for a in assessments if a["status"] != AssessmentStatus.AVAILABLE),
        "approval_blockers": sum(1 for d in sorted(gating) if status_of.get(d, Status.NOT_ASSESSED)
                                 is not Status.AVAILABLE),
        "products": len(subjects),
    }
    result = {"kpis": kpis, "domains": domains, "assessments": assessments, "dispositions": dispositions}
    result["result_hash"] = "sha256:" + hashlib.sha256(canonical_json(result)).hexdigest()
    return result


def product_coverage(policy: dict, evidence: Evidence) -> dict[str, set[str]]:
    """Per product id: the evidence domains whose readiness measures cover it (same rules as screen 11)."""
    subject = policy["subject"]["entity"]
    idx = _Index(evidence)
    out: dict[str, set[str]] = {}
    for (e, k) in evidence.records:
        if e != subject:
            continue
        pid = key_value(k)
        out[pid] = {d["domain"] for d in policy["domains"] if all(_covered(m, subject, k, idx) for m in d["measures"])}
    return out
