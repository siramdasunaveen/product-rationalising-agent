"""Assess and Recommend stages of the analysis workflow (BUILD_PLAN step 11).

Builds the run-wide assessment context from the pinned snapshot and pinned packs, assesses every ACTIVE candidate
of the analysis with ``app.domain.assessment``, persists score, dimensions, recommendation, impact, priority and
status, and keeps blocker records in step with the facts: a blocker that is raised again stays (same code), one
that no longer applies is RESOLVED, and one whose candidate disappeared is superseded.
"""

import hashlib
import json
from collections import defaultdict
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db.models import AnalysisRun, Blocker, Candidate, DetectorFinding, EvidenceSnapshot, Run
from app.domain import assessment as am
from app.domain import decision_readiness as dr
from app.domain import readiness as rd
from app.domain.artifacts import ArtifactKind
from app.domain.detectors import ProductFeatures
from app.domain.evidence import DOMAINS, EvidenceDomain
from app.services import readiness as readiness_service

ACTIVE = ("OPEN", "IN_PROGRESS")
MIN_RATE_BASELINE = 3  # products with order data needed for a family fallout baseline
_PENDING: dict[Any, dict[str, Any]] = {}


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                 default=str).encode()).hexdigest()


def _pinned(session: Session, run: Run, manifest: dict, role: str, kind: ArtifactKind) -> dict:
    from app.services.analysis import pinned_content

    return pinned_content(session, run.tenant_id, manifest, role, kind)[1]


def family_fallout(features: list[ProductFeatures]) -> dict[str | None, float | None]:
    by_family: dict[str | None, list[ProductFeatures]] = defaultdict(list)
    for f in features:
        if f.orders and f.fallout is not None:
            by_family[f.family].append(f)
    out: dict[str | None, float | None] = {}
    for fam in {f.family for f in features}:
        members = by_family.get(fam, [])
        orders = sum(m.orders or 0 for m in members)
        out[fam] = (sum(m.fallout or 0 for m in members) / orders) if len(members) >= MIN_RATE_BASELINE and orders \
            else None
    return out


def build_context(session: Session, run: Run, manifest: dict, features: list[ProductFeatures],
                  snap: EvidenceSnapshot) -> am.Context:
    readiness_policy = _pinned(session, run, manifest, "readinessPolicy", ArtifactKind.READINESS_POLICY)
    precedence = _pinned(session, run, manifest, "precedencePolicy", ArtifactKind.PRECEDENCE_POLICY)
    evidence = readiness_service._from_snapshot(session, snap)
    coverage = rd.product_coverage(readiness_policy, evidence)
    material = set(precedence.get("materialFields", []))
    conflicted: dict[str, list[str]] = defaultdict(list)
    for entity, key, fld in sorted(evidence.conflicted):
        if f"{entity}.{fld}" not in material:
            continue
        pid = rd.key_value(key) if entity == "Product" else evidence.records.get((entity, key), {}).get("productId")
        if isinstance(pid, str):
            conflicted[pid].append(f"{entity}.{fld}")
    labels = {d.value: DOMAINS[d].label for d in EvidenceDomain}
    return am.Context(
        products={f.product_id: f for f in features},
        coverage={f.product_id: coverage.get(f.product_id, set()) for f in features},
        stale_domains=set(evidence.stale_domains),
        conflicted=dict(conflicted),
        objectives=set(run.objectives),
        settings={k: v["value"] for k, v in manifest["settings"].items()},
        scoring=_pinned(session, run, manifest, "scoringProfile", ArtifactKind.SCORING_PROFILE),
        priority=_pinned(session, run, manifest, "priorityPolicy", ArtifactKind.PRIORITY_POLICY),
        dispositions=readiness_policy["dispositions"],
        domain_importance={d["domain"]: d["importance"] for d in readiness_policy["domains"]},
        domain_labels=labels,
        as_of=snap.approved_at.date(),
        family_fallout=family_fallout(features),
    )


def _inputs(session: Session, analysis: AnalysisRun) -> list[tuple[Candidate, am.CandidateInput]]:
    cands = list(session.scalars(select(Candidate).where(Candidate.analysis_id == analysis.id,
                                                         Candidate.status == "ACTIVE").order_by(Candidate.code)))
    findings: dict[Any, list[dict]] = defaultdict(list)
    for f in session.scalars(select(DetectorFinding).where(DetectorFinding.analysis_id == analysis.id)):
        if f.candidate_id:
            findings[f.candidate_id].append({"type": f.detector_type, "signal": f.signal, "factors": f.factors,
                                             "evidence_ids": f.evidence_ids})
    return [(c, am.CandidateInput(c.code, list(c.subjects), c.primary_type, list(c.supporting_types),
                                  findings[c.id])) for c in cands]


def assess(session: Session, run: Run, analysis: AnalysisRun, manifest: dict, features: list[ProductFeatures],
           snap: EvidenceSnapshot) -> dict[str, Any]:
    """Assess stage: Opportunity Score and the three dimensions of every active candidate."""
    ctx = build_context(session, run, manifest, features, snap)
    results = []
    for row, ci in _inputs(session, analysis):
        r = am.assess(ci, ctx)
        row.similarity = r["similarity"]
        row.opportunity_score = r["opportunity"]["score"]
        row.score_inputs = r["opportunity"]["inputs"]
        row.score_coverage = r["opportunity"]["coverage"]
        row.dimensions = r["dimensions"]
        row.guardrail = r["guardrail"]
        results.append((row, r))
    session.flush()
    _PENDING[analysis.id] = {"ctx": ctx, "results": results}
    scored = [row.opportunity_score for row, _ in results if row.opportunity_score is not None]
    return {"assessed": len(results), "scored": len(scored),
            "notAssessed": sum(1 for row, _ in results if row.guardrail == "NOT_ASSESSED")}


def _next_code(session: Session, run: Run) -> int:
    codes = session.scalars(select(Blocker.code).where(Blocker.run_id == run.id))
    return max((int(c.split("-")[1]) for c in codes), default=0) + 1


def sync_blockers(session: Session, run: Run, analysis: AnalysisRun, snap: EvidenceSnapshot,
                  raised: list[tuple[Candidate, am.BlockerDraft]], assessed_codes: set[str]) -> dict[str, int]:
    """Create new blockers, keep re-raised ones, resolve those that no longer apply, supersede orphans."""
    from app.services.outbox import emit

    existing = {b.dedupe_key: b for b in session.scalars(
        select(Blocker).where(Blocker.run_id == run.id, Blocker.tenant_id == run.tenant_id,
                              Blocker.state.in_(ACTIVE), Blocker.superseded.is_(False)))}
    nxt = _next_code(session, run)
    seen: set[str] = set()
    created = kept = resolved = superseded = 0
    for cand, d in raised:
        key = f"{cand.code}|{d.key}"
        seen.add(key)
        b = existing.get(key)
        if b is None:
            b = Blocker(tenant_id=run.tenant_id, run_id=run.id, analysis_id=analysis.id, candidate_id=cand.id,
                        candidate_code=cand.code, code=f"BLK-{nxt:04d}", dedupe_key=key, rule=d.rule,
                        category=d.category, state="OPEN", consequence=d.consequence, severity=d.severity,
                        domain=d.domain, subject=d.subject, message=d.message, owner_role=d.owner_role,
                        due_date=d.due_date, applicable=True, superseded=False, facts=d.facts)
            session.add(b)
            nxt += 1
            created += 1
            emit(session, tenant_id=run.tenant_id, event_type="BlockerCreated", aggregate_type="run",
                 aggregate_id=str(run.id), payload={"code": b.code, "candidate": cand.code, "rule": d.rule,
                                                    "severity": d.severity, "consequence": d.consequence})
        else:
            b.analysis_id, b.candidate_id = analysis.id, cand.id
            b.consequence, b.severity, b.message, b.facts = d.consequence, d.severity, d.message, d.facts
            b.updated_at = _now()
            kept += 1
    for key, b in existing.items():
        if key in seen:
            continue
        b.updated_at = _now()
        if b.candidate_code in assessed_codes:
            b.state, b.resolved_at, b.resolved_by = "RESOLVED", _now(), "system:assessment"
            b.resolution = f"No longer applies on {snap.code}"
            resolved += 1
            emit(session, tenant_id=run.tenant_id, event_type="BlockerResolved", aggregate_type="run",
                 aggregate_id=str(run.id), payload={"code": b.code, "candidate": b.candidate_code})
        else:
            b.superseded, b.applicable = True, False
            superseded += 1
    session.flush()
    return {"created": created, "kept": kept, "resolved": resolved, "superseded": superseded}


def recommend(session: Session, run: Run, analysis: AnalysisRun, manifest: dict, features: list[ProductFeatures],
              snap: EvidenceSnapshot) -> dict[str, Any]:
    """Recommend stage: recommendation, impact, priority, blockers and Candidate Status."""
    pending = _PENDING.pop(analysis.id, None)
    if pending is None:  # Assess ran in another process: recompute deterministically
        assess(session, run, analysis, manifest, features, snap)
        pending = _PENDING.pop(analysis.id)
    raised: list[tuple[Candidate, am.BlockerDraft]] = []
    for row, r in pending["results"]:
        rec, prio = r["recommendation"], r["priority"]
        row.recommendation = rec
        row.hidden = bool(rec.get("hidden"))
        row.impact = r["impact"]
        row.priority_index, row.base_priority, row.effective_priority = prio["index"], prio["base"], prio["effective"]
        row.priority_detail = {k: prio[k] for k in ("coverage", "overrides", "sunsetDays", "factors")}
        raised += [(row, d) for d in r["blockers"]]
    counts = sync_blockers(session, run, analysis, snap, raised,
                           {row.code for row, _ in pending["results"]})
    active = defaultdict(list)
    for b in session.scalars(select(Blocker).where(Blocker.run_id == run.id, Blocker.state.in_(ACTIVE),
                                                   Blocker.applicable.is_(True), Blocker.superseded.is_(False))):
        active[b.candidate_code].append({"category": b.category, "consequence": b.consequence})
    ctx: am.Context = pending["ctx"]
    blocker_rows = defaultdict(list)
    for b in session.scalars(select(Blocker).where(Blocker.run_id == run.id, Blocker.state.in_(ACTIVE),
                                                   Blocker.applicable.is_(True), Blocker.superseded.is_(False))):
        blocker_rows[b.candidate_code].append({"category": b.category, "consequence": b.consequence,
                                               "message": b.message})
    for row, r in pending["results"]:
        rec = dict(row.recommendation or {})
        rec["gateFacts"] = r["gateFacts"]
        if rec.get("action"):
            rec["decisionReadiness"] = dr.evaluate(
                rec["action"], rec.get("targetProductId"), row.primary_type, list(row.supporting_types),
                r["gateFacts"], ctx.settings, ctx.dispositions, blocker_rows.get(row.code, []), list(row.subjects))
        row.recommendation = rec
        row.candidate_status = am.candidate_status(active.get(row.code, []), r["guardrail"])
        row.assessment_hash = _hash({"score": row.opportunity_score, "inputs": row.score_inputs,
                                     "dimensions": row.dimensions, "guardrail": row.guardrail,
                                     "recommendation": row.recommendation, "impact": row.impact,
                                     "priority": [row.priority_index, row.base_priority, row.effective_priority,
                                                  row.priority_detail], "status": row.candidate_status})
    run.projection_version = (run.projection_version or 0) + 1
    session.flush()
    actions = defaultdict(int)
    for row, _ in pending["results"]:
        actions[(row.recommendation or {}).get("action") or "NONE"] += 1
    return {"recommendations": dict(sorted(actions.items())), "blockers": counts}


def result_digest(session: Session, analysis: AnalysisRun) -> list:
    rows = session.scalars(select(Candidate).where(Candidate.analysis_id == analysis.id).order_by(Candidate.code))
    return [[c.code, c.assessment_hash] for c in rows]


def blocked_count(session: Session, run: Run) -> int:
    return session.scalar(select(func.count()).select_from(Blocker).where(
        Blocker.run_id == run.id, Blocker.tenant_id == run.tenant_id, Blocker.state.in_(ACTIVE),
        Blocker.consequence == "BLOCKED", Blocker.applicable.is_(True), Blocker.superseded.is_(False))) or 0
