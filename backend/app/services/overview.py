"""Run Overview read model (screen 06). Deterministic projection of the authoritative stores; rebuildable.

metrics.sourceProducts  = distinct in-scope canonical products of the analysed snapshot
metrics.candidates      = ACTIVE candidates of the latest successful analysis
metrics.highPriority    = those with effectivePriority IN (HIGH, CRITICAL)  (never derived from the score)
metrics.blocked         = blocker RECORDS: state OPEN/IN_PROGRESS, consequence BLOCKED, applicable, not superseded
attention               = top N of those blockers by severity DESC, due date ASC, created ASC (+ total count)
Metrics are null (not 0) until the analysis that produces them has succeeded.
"""

from typing import Any

from sqlalchemy import case, func, select
from sqlalchemy.orm import Session

from app.db.models import Blocker, Candidate, EvidenceSnapshot, Run
from app.domain.runs import STATE_LABELS, RunState
from app.services import analysis as analysis_service
from app.services.assessment import ACTIVE, blocked_count

ATTENTION_TOP_N = 5
SEVERITY_RANK = case({"CRITICAL": 4, "HIGH": 3, "MEDIUM": 2, "LOW": 1}, value=Blocker.severity, else_=0)


def _attention_filter(run: Run):
    return (Blocker.run_id == run.id, Blocker.tenant_id == run.tenant_id, Blocker.state.in_(ACTIVE),
            Blocker.consequence == "BLOCKED", Blocker.applicable.is_(True), Blocker.superseded.is_(False))


def attention(session: Session, run: Run, limit: int = ATTENTION_TOP_N) -> tuple[list[Blocker], int]:
    total = session.scalar(select(func.count()).select_from(Blocker).where(*_attention_filter(run))) or 0
    rows = list(session.scalars(select(Blocker).where(*_attention_filter(run)).order_by(
        SEVERITY_RANK.desc(), Blocker.due_date.asc().nulls_last(), Blocker.created_at.asc(), Blocker.code)
        .limit(limit)))
    return rows, total


def overview(session: Session, run: Run) -> dict[str, Any]:
    latest = analysis_service.latest(session, run)
    ok = analysis_service.latest(session, run, succeeded=True)
    snap = session.get(EvidenceSnapshot, ok.snapshot_id) if ok else None
    if snap is None and latest is not None:
        snap = session.get(EvidenceSnapshot, latest.snapshot_id)
    metrics: dict[str, int | None] = {"sourceProducts": None, "candidates": None, "highPriority": None,
                                      "blocked": None}
    detector = None
    if ok is not None:
        active = select(Candidate).where(Candidate.analysis_id == ok.id, Candidate.status == "ACTIVE")
        metrics["sourceProducts"] = (ok.source_products or {}).get("sourceProducts")
        metrics["candidates"] = session.scalar(select(func.count()).select_from(active.subquery())) or 0
        metrics["highPriority"] = session.scalar(select(func.count()).select_from(
            active.where(Candidate.effective_priority.in_(("HIGH", "CRITICAL"))).subquery())) or 0
        metrics["blocked"] = blocked_count(session, run)
        d = ok.detector_summary or {}
        detector = {"rawFindings": d.get("rawFindings", 0), "duplicate": d.get("duplicate", 0),
                    "overlap": d.get("overlap", 0), "legacy": d.get("legacy", 0),
                    "complexity": d.get("complexity", 0), "operationalBurden": d.get("operational_burden", 0)}
    rows, total = attention(session, run) if ok is not None else ([], 0)
    sp = dict((ok.source_products if ok else None) or {})
    stages = (latest.stages if latest else
              [{"stage": s, "status": "NOT_STARTED", "started_at": None, "finished_at": None, "detail": None}
               for s in analysis_service.SYSTEM_STAGES + analysis_service.PEOPLE_STAGES])
    return {
        "run_id": run.id, "run_code": run.run_code, "run_name": run.name, "state": run.state,
        "state_label": STATE_LABELS.get(RunState(run.state), run.state),
        "snapshot": {"id": str(snap.id), "code": snap.code, "approved_at": snap.approved_at.isoformat()}
        if snap else None,
        "analysis": {"id": str(latest.id), "seq": latest.seq, "status": latest.status, "error": latest.error,
                     "finished_at": latest.finished_at.isoformat() if latest.finished_at else None}
        if latest else None,
        "metrics": metrics,
        "detectorSummary": detector,
        "counts": ok.counts if ok else None,
        "sourceProducts": {k: sp[k] for k in ("snapshotProducts", "sourceProducts", "excluded") if k in sp} or None,
        "stages": [{k: s.get(k) for k in ("stage", "status", "started_at", "finished_at")} for s in stages],
        "attention": [{"blocker_id": str(b.id), "code": b.code, "severity": b.severity, "subject": b.subject,
                       "candidate_id": str(b.candidate_id) if b.candidate_id else None,
                       "candidate_code": b.candidate_code, "message": b.message, "domain": b.domain,
                       "owner_role": b.owner_role, "due_date": b.due_date.isoformat() if b.due_date else None,
                       "created_at": b.created_at.isoformat()} for b in rows],
        "attentionTotal": total,
        "projectionVersion": run.projection_version or 0,
    }
