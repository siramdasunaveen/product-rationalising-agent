"""Candidate normalization (BUILD_PLAN step 10). Pure and deterministic.

Findings are grouped into Candidates by product set (grouping scope from the pinned detector pack):
candidateGroupKey = SHA-256(runId + snapshotId + groupingScope + sorted productIds + detector pack ref). One
Candidate per key, all its findings attached, primary type by the pack's precedence, supporting types kept,
evidence ids united, the strongest detector signal kept separately. Findings whose subjects are not in the snapshot
are INVALID; subjects outside the run's scope are OUT_OF_SCOPE. Nothing is deleted; every count reconciles:
candidates = raw findings - merged - invalid - out of scope - superseded.
"""

import hashlib
from collections import OrderedDict
from dataclasses import dataclass, field
from typing import Any

from app.domain.detectors import Finding


@dataclass
class CandidateDraft:
    group_key: str
    subjects: list[str]
    primary_type: str
    supporting_types: list[str]
    max_signal: int
    finding_keys: list[str]
    evidence_ids: list[str]
    status: str = "ACTIVE"
    reason: str | None = None
    lineage: dict[str, Any] = field(default_factory=dict)


@dataclass
class Normalised:
    candidates: list[CandidateDraft]
    finding_status: dict[str, str]  # finding key -> ATTACHED | MERGED | INVALID | OUT_OF_SCOPE
    counts: dict[str, int]


def group_key(run_id: str, snapshot_id: str, scope: str, subjects: list[str], pack_ref: str,
              action_class: str | None = None) -> str:
    parts = [run_id, snapshot_id, scope, ",".join(sorted(subjects)), pack_ref]
    if action_class:
        parts.append(action_class)
    return "sha256:" + hashlib.sha256("|".join(parts).encode()).hexdigest()


ACTION_CLASS = {"DUPLICATE": "COMMERCIAL", "OVERLAP": "COMMERCIAL", "LEGACY": "LIFECYCLE",
                "COMPLEXITY": "OPERATIONAL", "OPERATIONAL_BURDEN": "OPERATIONAL"}


def normalise(findings: list[Finding], *, run_id: str, snapshot_id: str, pack: dict, pack_ref: str,
              snapshot_products: set[str], in_scope: set[str]) -> Normalised:
    scope = pack["grouping"]["scope"]
    precedence = pack["primaryTypePrecedence"]
    groups: "OrderedDict[str, list[Finding]]" = OrderedDict()
    status: dict[str, str] = {}
    invalid = out_of_scope = 0
    for f in sorted(findings, key=lambda x: (x.subjects, precedence.index(x.detector_type), x.key)):
        missing = [s for s in f.subjects if s not in snapshot_products]
        if missing:
            status[f.key] = "INVALID"
            invalid += 1
            continue
        if any(s not in in_scope for s in f.subjects):
            status[f.key] = "OUT_OF_SCOPE"
            out_of_scope += 1
            continue
        ac = ACTION_CLASS[f.detector_type] if scope == "PRODUCT_SET_AND_ACTION_CLASS" else None
        key = group_key(run_id, snapshot_id, scope, f.subjects, pack_ref, ac)
        status[f.key] = "MERGED" if key in groups else "ATTACHED"
        groups.setdefault(key, []).append(f)
    candidates = []
    for key, fs in groups.items():
        types = sorted({f.detector_type for f in fs}, key=precedence.index)
        candidates.append(CandidateDraft(
            group_key=key, subjects=sorted(fs[0].subjects), primary_type=types[0], supporting_types=types[1:],
            max_signal=max(f.signal for f in fs), finding_keys=[f.key for f in fs],
            evidence_ids=sorted({e for f in fs for e in f.evidence_ids})))
    merged = sum(1 for v in status.values() if v == "MERGED")
    counts = {"rawFindings": len(findings), "merged": merged, "invalid": invalid, "outOfScope": out_of_scope,
              "superseded": 0, "candidates": len(candidates)}
    if counts["rawFindings"] - merged - invalid - out_of_scope != len(candidates):
        raise ValueError(f"Batch counts do not reconcile: {counts}")
    return Normalised(candidates, status, counts)
