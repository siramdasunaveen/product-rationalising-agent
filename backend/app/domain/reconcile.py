"""Reconciliation (BUILD_PLAN step 6). Pure and deterministic: no I/O, no AI.

Input: the staged records of each source (one successful mapping apply per source) plus the published precedence
policy. Output: one reconciled record per canonical entity key, field by field:

- sources agree (or only one source has the field) -> that value, every supplying source listed;
- sources differ on a NON-material field -> settled automatically by precedence, then freshness, then source
  name (the reason is recorded and shown);
- sources differ on a MATERIAL field -> a conflict for the Data Steward: SELECT_SOURCE, MANUAL (rationale) or
  DEFER (field left empty and flagged conflicted). Until decided the field is left out and the conflict is OPEN.

Identity is the canonical key produced by mapping (keys and crosswalks); similar names are only listed as possible
duplicates, never merged (CLAUDE.md rule 7). A decision is kept across re-runs while the conflicting values are
unchanged (same values fingerprint); if a source changes its value the conflict reopens.
"""

import hashlib
import json
import re
from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from app.domain.artifact_content import find_field
from app.domain.artifacts import canonical_json


class ConflictStatus:
    OPEN = "OPEN"
    AUTO_RESOLVED = "AUTO_RESOLVED"
    RESOLVED = "RESOLVED"
    DEFERRED = "DEFERRED"


class Decision:
    SELECT_SOURCE = "SELECT_SOURCE"
    MANUAL = "MANUAL"
    DEFER = "DEFER"


DECISIONS = (Decision.SELECT_SOURCE, Decision.MANUAL, Decision.DEFER)
NAME_FIELDS = {"Product": "productName", "Offer": "offerName", "BillingSystem": "billingSystemName",
               "ResourceSpec": "resourceName"}


@dataclass(frozen=True)
class SourceInfo:
    source_id: str
    name: str
    domain: str
    system: str
    as_of: date
    execution_id: str


@dataclass(frozen=True)
class SourceRecord:
    entity: str
    key: str
    values: dict[str, Any]
    lineage: dict[str, int] = field(default_factory=dict)  # field -> field_lineage id (step 5)


@dataclass(frozen=True)
class SourceEdge:
    name: str
    from_entity: str
    from_key: str
    to_entity: str
    to_key: str


@dataclass
class SourceData:
    info: SourceInfo
    records: list[SourceRecord]
    edges: list[SourceEdge]


@dataclass(frozen=True)
class StewardDecision:
    decision: str  # SELECT_SOURCE | MANUAL | DEFER
    decision_id: str
    source_id: str | None = None
    value: Any = None
    rationale: str | None = None


@dataclass
class Policy:
    content: dict

    def freshness_days(self, domain: str) -> int:
        return self.content.get("freshnessDays", {}).get(domain, self.content.get("defaultFreshnessDays", 90))

    def order_for(self, entity: str, field_name: str) -> tuple[str | None, list[str]]:
        exact = f"{entity}.{field_name}"
        wildcard = f"{entity}.*"
        for selector in (exact, wildcard):
            for p in self.content.get("precedence", []):
                if p["field"] == selector:
                    return selector, p["order"]
        return None, []

    def material(self, entity: str, field_name: str) -> bool:
        return f"{entity}.{field_name}" in set(self.content.get("materialFields", []))


def is_stale(info: SourceInfo, policy: Policy, today: date) -> bool:
    return info.as_of < today - timedelta(days=policy.freshness_days(info.domain))


def _rank(info: SourceInfo, order: list[str]) -> tuple[int, str | None]:
    for i, token in enumerate(order):
        domain, _, system = token.partition(":")
        if info.domain == domain and (not system or info.system == system):
            return i, token
    return len(order), None


def _vjson(v: Any) -> str:
    return canonical_json(v).decode("utf-8")


def short_hash(*parts: Any) -> str:
    return hashlib.sha256(canonical_json(list(parts))).hexdigest()[:16]


def conflict_key(entity: str, key: str, field_name: str) -> str:
    return short_hash(entity, key, field_name)


def values_fingerprint(candidates: list[tuple[SourceInfo, Any]]) -> str:
    return short_hash(sorted([info.source_id, _vjson(v)] for info, v in candidates))


@dataclass
class Conflict:
    conflict_key: str
    entity: str
    key: str
    field: str
    material: bool
    fingerprint: str
    candidates: list[dict[str, Any]]
    recommended_source_id: str
    reason: str
    status: str
    decision_id: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return self.__dict__.copy()


@dataclass
class ReconciledRecord:
    entity: str
    key: str
    values: dict[str, Any]
    provenance: dict[str, dict[str, Any]]  # field -> {rule, sources, chosen_source, conflict_key, stale, lineage}
    sources: list[str]


@dataclass
class ReconciledEdge:
    name: str
    from_entity: str
    from_key: str
    to_entity: str
    to_key: str
    sources: list[str]


@dataclass
class Result:
    records: dict[tuple[str, str], ReconciledRecord] = field(default_factory=dict)
    edges: list[ReconciledEdge] = field(default_factory=list)
    conflicts: list[Conflict] = field(default_factory=list)
    stale_sources: list[dict[str, Any]] = field(default_factory=list)
    dangling: list[dict[str, Any]] = field(default_factory=list)
    possible_duplicates: list[dict[str, Any]] = field(default_factory=list)

    def counts(self) -> dict[str, Any]:
        by_status: dict[str, int] = defaultdict(int)
        for c in self.conflicts:
            by_status[c.status] += 1
        per_entity: dict[str, int] = defaultdict(int)
        for e, _ in self.records:
            per_entity[e] += 1
        per_rel: dict[str, int] = defaultdict(int)
        for r in self.edges:
            per_rel[r.name] += 1
        return {"records": dict(sorted(per_entity.items())), "relationships": dict(sorted(per_rel.items())),
                "conflicts": {s: by_status.get(s, 0) for s in (ConflictStatus.OPEN, ConflictStatus.AUTO_RESOLVED,
                                                                ConflictStatus.RESOLVED, ConflictStatus.DEFERRED)},
                "open_material_conflicts": sum(1 for c in self.conflicts
                                               if c.status == ConflictStatus.OPEN and c.material),
                "stale_sources": len(self.stale_sources), "dangling_references": len(self.dangling),
                "possible_duplicates": len(self.possible_duplicates)}


def _pick(cands: list[tuple[SourceInfo, Any]], entity: str, field_name: str, policy: Policy,
          today: date) -> tuple[SourceInfo, str]:
    selector, order = policy.order_for(entity, field_name)

    def sort_key(c: tuple[SourceInfo, Any]):
        info = c[0]
        return (_rank(info, order)[0], is_stale(info, policy, today), -info.as_of.toordinal(), info.name,
                info.source_id)

    ranked = sorted(cands, key=sort_key)
    best, runner = ranked[0][0], ranked[1][0]
    b_rank, token = _rank(best, order)
    if b_rank < _rank(runner, order)[0]:
        return best, f"Precedence: {token} owns {selector}"
    if is_stale(best, policy, today) != is_stale(runner, policy, today):
        return best, f"Freshness: {best.name} is within its freshness limit; {runner.name} is stale"
    if best.as_of != runner.as_of:
        return best, f"Freshness: newer data ({best.name}, as of {best.as_of.isoformat()})"
    return best, f"Tie: same precedence and date; chose {best.name} by name order"


def reconcile(sources: list[SourceData], policy: Policy, canonical: dict, today: date,
              decisions: dict[tuple[str, str], StewardDecision] | None = None) -> Result:
    decisions = decisions or {}
    result = Result()
    info_by_id = {s.info.source_id: s.info for s in sources}
    for s in sorted(sources, key=lambda s: s.info.name):
        if is_stale(s.info, policy, today):
            result.stale_sources.append({"source_id": s.info.source_id, "name": s.info.name,
                                         "domain": s.info.domain, "as_of": s.info.as_of.isoformat(),
                                         "limit_days": policy.freshness_days(s.info.domain)})

    grouped: dict[tuple[str, str], list[tuple[SourceInfo, SourceRecord]]] = defaultdict(list)
    for s in sources:
        for r in s.records:
            grouped[(r.entity, r.key)].append((s.info, r))

    for (entity, key) in sorted(grouped):
        entries = grouped[(entity, key)]
        values: dict[str, Any] = {}
        provenance: dict[str, dict[str, Any]] = {}
        fields_ = sorted({f for _, r in entries for f in r.values})
        for f in fields_:
            cands = [(info, r.values[f]) for info, r in entries if f in r.values]
            lineage = {info.source_id: r.lineage.get(f) for info, r in entries if f in r.values}
            distinct = {_vjson(v) for _, v in cands}
            all_stale = all(is_stale(info, policy, today) for info, _ in cands)
            if len(distinct) == 1:
                values[f] = cands[0][1]
                provenance[f] = {"rule": "AGREED" if len(cands) > 1 else "SINGLE_SOURCE",
                                 "sources": sorted(i.source_id for i, _ in cands), "chosen_source": None,
                                 "conflict_key": None, "stale": all_stale, "lineage": lineage}
                continue
            best, reason = _pick(cands, entity, f, policy, today)
            ck, fp = conflict_key(entity, key, f), values_fingerprint(cands)
            material = policy.material(entity, f)
            conflict = Conflict(
                conflict_key=ck, entity=entity, key=key, field=f, material=material, fingerprint=fp,
                candidates=[{"source_id": i.source_id, "source_name": i.name, "domain": i.domain, "system": i.system,
                             "as_of": i.as_of.isoformat(), "stale": is_stale(i, policy, today), "value": v,
                             "lineage_id": lineage.get(i.source_id)}
                            for i, v in sorted(cands, key=lambda c: c[0].name)],
                recommended_source_id=best.source_id, reason=reason,
                status=ConflictStatus.AUTO_RESOLVED if not material else ConflictStatus.OPEN)
            decision = decisions.get((ck, fp))
            chosen: str | None = None
            if decision is not None:
                if decision.decision == Decision.SELECT_SOURCE and decision.source_id in lineage:
                    conflict.status, chosen = ConflictStatus.RESOLVED, decision.source_id
                    values[f] = next(v for i, v in cands if i.source_id == chosen)
                    rule = "STEWARD_SELECTED"
                elif decision.decision == Decision.MANUAL:
                    conflict.status = ConflictStatus.RESOLVED
                    values[f] = decision.value
                    rule = "STEWARD_MANUAL"
                elif decision.decision == Decision.DEFER:
                    conflict.status = ConflictStatus.DEFERRED
                    rule = "DEFERRED"
                else:  # decision for a source that no longer supplies this field: reopen
                    decision = None
                if decision is not None:
                    conflict.decision_id = decision.decision_id
            if decision is None:
                if material:
                    rule = "OPEN"
                else:
                    chosen = best.source_id
                    values[f] = next(v for i, v in cands if i.source_id == chosen)
                    rule = "PRECEDENCE" if reason.startswith(("Precedence", "Tie")) else "FRESHNESS"
            provenance[f] = {"rule": rule, "sources": [chosen] if chosen else sorted(lineage), "chosen_source": chosen,
                             "conflict_key": ck, "stale": is_stale(info_by_id[chosen], policy, today) if chosen
                             else all_stale, "lineage": lineage, "reason": reason,
                             "conflicted": rule in ("OPEN", "DEFERRED")}
            result.conflicts.append(conflict)
        result.records[(entity, key)] = ReconciledRecord(entity, key, values, provenance,
                                                         sorted({i.source_id for i, _ in entries}))

    edge_sources: dict[tuple[str, str, str, str, str], set[str]] = defaultdict(set)
    for s in sources:
        for e in s.edges:
            edge_sources[(e.name, e.from_entity, e.from_key, e.to_entity, e.to_key)].add(s.info.source_id)
    result.edges = [ReconciledEdge(*k, sources=sorted(v)) for k, v in sorted(edge_sources.items())]

    _check_references(result, canonical)
    _possible_duplicates(result)
    return result


def _check_references(result: Result, canonical: dict) -> None:
    """Links to entities that no source supplied (warnings: the target may be out of scope or in a later file)."""
    keys_by_entity: dict[str, set[str]] = defaultdict(set)
    for e, k in result.records:
        keys_by_entity[e].add(k)
    for edge in result.edges:
        for side, entity, key in (("from", edge.from_entity, edge.from_key), ("to", edge.to_entity, edge.to_key)):
            if key not in keys_by_entity.get(entity, set()):
                result.dangling.append({"kind": "relationship", "name": edge.name, "side": side, "entity": entity,
                                        "key": key, "sources": edge.sources})
    for (entity, key), rec in sorted(result.records.items()):
        for f, v in sorted(rec.values.items()):
            fdef = find_field(canonical, entity, f)
            if fdef and fdef.get("type") == "reference" and v is not None:
                target = fdef["references"]
                ref_key = _vjson([v])
                if ref_key not in keys_by_entity.get(target, set()):
                    result.dangling.append({"kind": "reference", "name": f"{entity}.{f}", "entity": target,
                                            "key": ref_key, "from_record": key, "sources": rec.sources})


def _norm_name(text: Any) -> str:
    return re.sub(r"[^a-z0-9]+", " ", str(text).casefold()).strip()


def _possible_duplicates(result: Result) -> None:
    """Same normalised name, different keys: a suggestion for a person, never a merge."""
    groups: dict[tuple[str, str], list[str]] = defaultdict(list)
    for (entity, key), rec in sorted(result.records.items()):
        name_field = NAME_FIELDS.get(entity)
        if name_field and rec.values.get(name_field):
            groups[(entity, _norm_name(rec.values[name_field]))].append(key)
    for (entity, name), keys in sorted(groups.items()):
        if len(keys) > 1:
            result.possible_duplicates.append({"entity": entity, "normalised_name": name, "keys": keys})


def result_hash(result: Result) -> str:
    body = {
        "records": [{"entity": r.entity, "key": r.key, "values": r.values,
                     "rules": {f: p["rule"] for f, p in sorted(r.provenance.items())}}
                    for _, r in sorted(result.records.items())],
        "edges": [[e.name, e.from_entity, e.from_key, e.to_entity, e.to_key] for e in result.edges],
        "conflicts": sorted([c.conflict_key, c.fingerprint, c.status] for c in result.conflicts),
    }
    return "sha256:" + hashlib.sha256(canonical_json(body)).hexdigest()


# ---------------------------------------------------------------------------
# Snapshot facts: one fact per canonical field, with a stable evidence id
# ---------------------------------------------------------------------------

def evidence_id(*parts: Any) -> str:
    """Stable id of a fact: the same entity, key, field and value give the same id in every snapshot."""
    return "EV-" + hashlib.sha256(canonical_json(list(parts))).hexdigest()[:12].upper()


def snapshot_facts(result: Result) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    facts: list[dict[str, Any]] = []
    for (entity, key), rec in sorted(result.records.items()):
        for f in sorted(rec.provenance):
            prov = rec.provenance[f]
            value = rec.values.get(f)
            facts.append({"evidence_id": evidence_id(entity, key, f, value), "entity": entity, "record_key": key,
                          "field": f, "value": value, "provenance": prov,
                          "stale": bool(prov.get("stale")), "conflicted": bool(prov.get("conflicted"))})
    edges = [{"evidence_id": evidence_id("REL", e.name, e.from_entity, e.from_key, e.to_entity, e.to_key),
              "name": e.name, "from_entity": e.from_entity, "from_key": e.from_key, "to_entity": e.to_entity,
              "to_key": e.to_key, "sources": e.sources} for e in result.edges]
    return facts, edges


def snapshot_content_hash(facts: list[dict[str, Any]], edges: list[dict[str, Any]]) -> str:
    body = {"facts": [[f["evidence_id"], f["entity"], f["record_key"], f["field"], f["value"], f["stale"],
                       f["conflicted"]] for f in sorted(facts, key=lambda x: x["evidence_id"])],
            "edges": sorted(e["evidence_id"] for e in edges)}
    return "sha256:" + hashlib.sha256(canonical_json(body)).hexdigest()


def json_value(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, sort_keys=True)
