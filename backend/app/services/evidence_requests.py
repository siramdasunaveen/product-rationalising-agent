"""Evidence gaps and requests (BUILD_PLAN step 13, screens 13-15).

Gaps come from the active EVIDENCE blockers the assessment raised (rule 6 and the technical gate). A request is
created per gap (candidate + domain) with the logical contract; the user assigns owner, due date and fulfilment
channel. Returned evidence arrives through the Evidence Hub as a new approved (child) snapshot; every open request
that snapshot refreshes becomes RECEIVED. Accepting runs a reassessment on the child snapshot with the run's
pinned manifest; the reassessment resolves the blockers, and requests whose gap closed are completed or superseded.
"""

import uuid
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.registry import Platform
from app.db.models import (
    AnalysisRun,
    Blocker,
    Candidate,
    EvidenceRequest,
    EvidenceRequestEvent,
    EvidenceSnapshot,
    Run,
)
from app.domain import evidence_requests as er
from app.domain.artifacts import ArtifactKind
from app.domain.common import FieldError
from app.domain.evidence import DOMAINS, EvidenceDomain
from app.domain.readiness import product_coverage
from app.domain.runs import RunState
from app.ports.audit import AuditRecord
from app.ports.auth import Principal
from app.services import analysis as analysis_service
from app.services.analysis import manifest_for, pinned_content
from app.services.assessment import ACTIVE as ACTIVE_BLOCKERS
from app.services.errors import ServiceError
from app.services.evidence import template_for
from app.services.outbox import emit
from app.services.readiness import _from_snapshot

FIRST_CODE = 101
EVIDENCE_RULES = ("EVIDENCE_REQUIRED", "EVIDENCE_STALE")


class RequestError(ServiceError):
    code = "evidence_request_error"


class RequestNotFound(RequestError):
    status_code = 404
    code = "not_found"


class RequestConflict(RequestError):
    status_code = 409
    code = "conflict"


class RequestValidation(RequestError):
    status_code = 422
    code = "validation_failed"


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(session: Session, platform: Platform, principal: Principal, action: str, req: EvidenceRequest,
           payload: dict[str, Any]) -> None:
    platform.audit.record(AuditRecord(
        tenant_id=principal.tenant_id, actor_id=principal.user_id, actor_roles=principal.roles,
        auth_context=principal.auth_context, action=action, entity_type="evidence_request", entity_id=str(req.id),
        payload={"run_id": str(req.run_id), "code": req.code, **payload}), session=session)


def _event(session: Session, req: EvidenceRequest, to: str, actor: str, note: str | None = None,
           detail: dict | None = None) -> None:
    session.add(EvidenceRequestEvent(tenant_id=req.tenant_id, request_id=req.id, from_status=req.status,
                                     to_status=to, actor_id=actor, note=note, detail=detail or {}))
    emit(session, tenant_id=req.tenant_id, event_type="EvidenceRequestStatusChanged", aggregate_type="run",
         aggregate_id=str(req.run_id), payload={"code": req.code, "from": req.status, "to": to})
    req.status = to
    req.updated_at = _now()
    if to in er.CLOSED:
        req.closed_at = _now()


def _move(session: Session, req: EvidenceRequest, to: str, actor: str, note: str | None = None,
          detail: dict | None = None) -> None:
    if not er.can_transition(req.status, to):
        raise RequestConflict(f"{req.code} is {req.status.lower().replace('_', ' ')}; it cannot become "
                              f"{to.lower().replace('_', ' ')}")
    _event(session, req, to, actor, note, detail)


def _next_code(session: Session, run: Run) -> int:
    codes = session.scalars(select(EvidenceRequest.code).where(EvidenceRequest.run_id == run.id))
    return max((int(c.split("-")[1]) for c in codes), default=FIRST_CODE - 1) + 1


def _executions(snap: EvidenceSnapshot, domain: str) -> list[str]:
    return sorted({i["execution_id"] for i in (snap.inputs or []) if i.get("domain") == domain and i.get("execution_id")})


# ---------------------------------------------------------------------------
# Gap detection
# ---------------------------------------------------------------------------

def _gap_blockers(session: Session, run: Run, candidate_code: str | None) -> list[Blocker]:
    stmt = select(Blocker).where(Blocker.run_id == run.id, Blocker.tenant_id == run.tenant_id,
                                 Blocker.category == "EVIDENCE", Blocker.rule.in_(EVIDENCE_RULES),
                                 Blocker.state.in_(ACTIVE_BLOCKERS), Blocker.applicable.is_(True),
                                 Blocker.superseded.is_(False))
    if candidate_code:
        stmt = stmt.where(Blocker.candidate_code == candidate_code)
    return list(session.scalars(stmt.order_by(Blocker.code)))


def detect(session: Session, platform: Platform, principal: Principal, run: Run,
           candidate_code: str | None = None) -> list[EvidenceRequest]:
    """Create a DRAFT request for every gap without an active request; return all active requests in scope."""
    a = analysis_service.latest(session, run, succeeded=True)
    if a is None:
        raise RequestConflict("The run has no assessed candidates yet")
    manifest = manifest_for(session, run).manifest
    policy = pinned_content(session, run.tenant_id, manifest, "readinessPolicy", ArtifactKind.READINESS_POLICY)[1]
    freshness = {d["domain"]: d.get("freshnessDays") for d in policy.get("domains", [])}
    labels = {d["domain"]: d.get("label") or DOMAINS[d["domain"]].label for d in policy.get("domains", [])
              if d["domain"] in DOMAINS}
    existing = {(r.candidate_code, r.domain): r for r in session.scalars(select(EvidenceRequest).where(
        EvidenceRequest.run_id == run.id, EvidenceRequest.tenant_id == run.tenant_id,
        EvidenceRequest.status.in_(er.ACTIVE)))}
    nxt = _next_code(session, run)
    out: list[EvidenceRequest] = []
    for b in _gap_blockers(session, run, candidate_code):
        domain = (b.facts or {}).get("domain") or (b.facts or {}).get("key")
        if not domain or domain not in DOMAINS:
            continue
        key = (b.candidate_code, domain)
        if key in existing:
            out.append(existing[key])
            continue
        cand = session.get(Candidate, b.candidate_id) if b.candidate_id else None
        snap = session.get(EvidenceSnapshot, a.snapshot_id)
        rec = (cand.recommendation if cand else None) or {}
        action = (b.facts or {}).get("action") or rec.get("action")
        stale = b.rule == "EVIDENCE_STALE"
        products = (b.facts or {}).get("products") or (list(cand.subjects) if cand else [])
        req = EvidenceRequest(
            tenant_id=run.tenant_id, run_id=run.id, code=f"ER-{nxt}", candidate_code=b.candidate_code,
            candidate_id=b.candidate_id, blocker_code=b.code, blocker_id=b.id, domain=domain, action=action,
            trigger=f"{b.candidate_code} · {(action or 'Assessment').title()}",
            evidence_needed=(f"{labels.get(domain, domain)} refresh" if stale
                             else f"{labels.get(domain, domain)} for {', '.join(products[:3])}")[:200],
            decision_impact=er.decision_impact(action, domain, stale)[:200], consequence=b.consequence,
            priority=b.severity, contract=er.contract(domain, action or "INVESTIGATE", products, stale=stale,
                                                     freshness_days=freshness.get(domain)),
            owner_role=b.owner_role or "data_steward", owner_id=None, due_date=b.due_date, status="DRAFT",
            snapshot_id=snap.id, baseline_executions=_executions(snap, domain), reassessment_status="NONE")
        session.add(req)
        session.flush()
        session.add(EvidenceRequestEvent(tenant_id=run.tenant_id, request_id=req.id, from_status=None,
                                         to_status="DRAFT", actor_id=principal.user_id,
                                         detail={"blocker": b.code, "snapshot": snap.code}))
        emit(session, tenant_id=run.tenant_id, event_type="EvidenceRequestCreated", aggregate_type="run",
             aggregate_id=str(run.id), payload={"code": req.code, "candidate": b.candidate_code, "domain": domain})
        _audit(session, platform, principal, "evidence_request.created", req,
               {"candidate": b.candidate_code, "domain": domain, "blocker": b.code})
        existing[key] = req
        out.append(req)
        nxt += 1
    session.commit()
    return out


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def list_requests(session: Session, tenant_id: str, run: Run | None, *, status: list[str] | None = None,
                  domain: str | None = None, candidate: str | None = None, owner_id: str | None = None,
                  query: str | None = None) -> list[EvidenceRequest]:
    stmt = select(EvidenceRequest).where(EvidenceRequest.tenant_id == tenant_id)
    if run is not None:
        stmt = stmt.where(EvidenceRequest.run_id == run.id)
    if status:
        stmt = stmt.where(EvidenceRequest.status.in_(status))
    if domain:
        stmt = stmt.where(EvidenceRequest.domain == domain)
    if candidate:
        stmt = stmt.where(EvidenceRequest.candidate_code == candidate)
    if owner_id:
        stmt = stmt.where(EvidenceRequest.owner_id == owner_id)
    rows = list(session.scalars(stmt.order_by(EvidenceRequest.created_at, EvidenceRequest.code)))
    if query:
        q = query.strip().lower()
        rows = [r for r in rows if q in " ".join([r.code, r.candidate_code, r.trigger, r.evidence_needed,
                                                   r.owner_id or "", r.owner_role]).lower()]
    return rows


def get(session: Session, run: Run, code: str) -> EvidenceRequest:
    req = session.scalar(select(EvidenceRequest).where(EvidenceRequest.run_id == run.id,
                                                       EvidenceRequest.tenant_id == run.tenant_id,
                                                       EvidenceRequest.code == code.upper()))
    if req is None:
        raise RequestNotFound("Evidence request not found")
    return req


def history(session: Session, req: EvidenceRequest) -> list[EvidenceRequestEvent]:
    return list(session.scalars(select(EvidenceRequestEvent).where(EvidenceRequestEvent.request_id == req.id)
                                .order_by(EvidenceRequestEvent.created_at)))


def open_needs(session: Session, run: Run, candidate_code: str) -> int:
    return len(list_requests(session, run.tenant_id, run, status=list(er.ACTIVE), candidate=candidate_code))


def to_dict(session: Session, req: EvidenceRequest, *, with_history: bool = False) -> dict[str, Any]:
    snap = session.get(EvidenceSnapshot, req.snapshot_id)
    received = session.get(EvidenceSnapshot, req.received_snapshot_id) if req.received_snapshot_id else None
    accepted = session.get(EvidenceSnapshot, req.accepted_snapshot_id) if req.accepted_snapshot_id else None
    run = session.get(Run, req.run_id)
    out = {
        "id": str(req.id), "code": req.code, "runId": str(req.run_id), "runCode": run.run_code if run else None,
        "candidateCode": req.candidate_code, "candidateId": str(req.candidate_id) if req.candidate_id else None,
        "blockerCode": req.blocker_code, "domain": req.domain, "domainLabel": DOMAINS[req.domain].label
        if req.domain in DOMAINS else req.domain, "action": req.action, "trigger": req.trigger,
        "evidenceNeeded": req.evidence_needed, "decisionImpact": req.decision_impact, "consequence": req.consequence,
        "priority": req.priority, "contract": req.contract, "ownerRole": req.owner_role, "ownerId": req.owner_id,
        "dueDate": req.due_date.isoformat() if req.due_date else None, "fulfilmentMethod": req.fulfilment_method,
        "fulfilmentJob": req.fulfilment_job, "status": req.status, "statusReason": req.status_reason,
        "snapshot": {"id": str(snap.id), "code": snap.code} if snap else None,
        "receivedSnapshot": {"id": str(received.id), "code": received.code} if received else None,
        "acceptedSnapshot": {"id": str(accepted.id), "code": accepted.code} if accepted else None,
        "reassessmentAnalysisId": str(req.reassessment_analysis_id) if req.reassessment_analysis_id else None,
        "reassessmentStatus": req.reassessment_status, "createdAt": req.created_at.isoformat(),
        "updatedAt": req.updated_at.isoformat(), "closedAt": req.closed_at.isoformat() if req.closed_at else None,
        "fulfilmentMethods": [{"key": k, "label": v[0], "note": v[1], "recommended": k == "SECURE_QUERY",
                               "available": True} for k, v in er.FULFILMENT_LABELS.items()],
    }
    if with_history:
        out["history"] = [{"from": e.from_status, "to": e.to_status, "actorId": e.actor_id, "note": e.note,
                           "detail": e.detail, "at": e.created_at.isoformat()} for e in history(session, req)]
    return out


# ---------------------------------------------------------------------------
# Lifecycle (user actions)
# ---------------------------------------------------------------------------

def assign(session: Session, platform: Platform, principal: Principal, run: Run, code: str, *,
           owner_id: str | None, due_date: date | None, fulfilment_method: str | None) -> EvidenceRequest:
    """DRAFT/OPEN -> OPEN: the only user-editable fields are owner, due date and fulfilment channel."""
    req = get(session, run, code)
    errors = []
    if fulfilment_method is not None and fulfilment_method not in er.FULFILMENT_METHODS:
        errors.append(FieldError("fulfilmentMethod", f"Choose one of {', '.join(er.FULFILMENT_METHODS)}"))
    if due_date is not None and due_date < date.today():
        errors.append(FieldError("dueDate", "The due date cannot be in the past"))
    if errors:
        raise RequestValidation("The assignment is not valid", errors)
    if req.status not in ("DRAFT", "OPEN", "IN_PROGRESS", "REJECTED"):
        raise RequestConflict(f"{req.code} is {req.status.lower().replace('_', ' ')} and cannot be reassigned")
    req.owner_id = owner_id if owner_id is not None else req.owner_id
    req.due_date = due_date or req.due_date
    req.fulfilment_method = fulfilment_method or req.fulfilment_method or "SECURE_QUERY"
    if req.status in ("DRAFT", "REJECTED", "OPEN"):
        _move(session, req, "OPEN", principal.user_id, detail={"ownerId": req.owner_id,
                                                                "dueDate": req.due_date.isoformat() if req.due_date else None,
                                                                "fulfilmentMethod": req.fulfilment_method})
    else:
        req.updated_at = _now()
    _audit(session, platform, principal, "evidence_request.assigned", req,
           {"owner_id": req.owner_id, "due_date": req.due_date.isoformat() if req.due_date else None,
            "fulfilment_method": req.fulfilment_method})
    session.commit()
    return req


def start(session: Session, platform: Platform, principal: Principal, run: Run, code: str) -> EvidenceRequest:
    """OPEN -> IN_PROGRESS: creates the channel-specific fulfilment job; the contract never changes."""
    req = get(session, run, code)
    if req.status != "OPEN":
        raise RequestConflict(f"{req.code} is {req.status.lower().replace('_', ' ')}; assign it first")
    method = req.fulfilment_method or "SECURE_QUERY"
    job: dict[str, Any] = {"channel": method, "startedAt": _now().isoformat(), "startedBy": principal.user_id}
    if method == "EXCEL_AGGREGATE":
        tpl = template_for(session, run.tenant_id, EvidenceDomain(req.domain)) if req.domain in DOMAINS else None
        job.update({"template": tpl[0] if tpl else None,
                    "templatePath": f"/runs/{run.id}/evidence/{req.domain}/template",
                    "uploadPath": f"/runs/{run.id}/evidence/{req.domain}/uploads",
                    "instructions": "Download the approved template, fill the aggregates for the listed products, "
                                    "upload it in the Evidence Hub, map, apply, reconcile and approve the snapshot."})
    else:
        job.update({"connector": None, "state": "AWAITING_CUSTOMER",
                    "instructions": "The customer integration layer fulfils the contract and returns the aggregate "
                                    "through the Evidence Hub (live connectors arrive in build step 15)."})
    req.fulfilment_job = job
    _move(session, req, "IN_PROGRESS", principal.user_id, detail={"channel": method})
    emit(session, tenant_id=run.tenant_id, event_type="EvidenceFulfilmentStarted", aggregate_type="run",
         aggregate_id=str(run.id), payload={"code": req.code, "channel": method})
    _audit(session, platform, principal, "evidence_request.started", req, {"channel": method})
    session.commit()
    return req


def on_snapshot_approved(session: Session, run: Run, snap: EvidenceSnapshot) -> list[EvidenceRequest]:
    """System: a new approved snapshot is a return for every waiting request whose domain it refreshes."""
    received = []
    for req in list_requests(session, run.tenant_id, run, status=["OPEN", "IN_PROGRESS", "REJECTED"]):
        if er.compatible({"domain": req.domain, "baselineExecutions": req.baseline_executions}, snap.inputs or []):
            req.received_snapshot_id = snap.id
            req.status_reason = None
            _event(session, req, "RECEIVED", "system:snapshot", detail={"snapshot": snap.code})
            emit(session, tenant_id=run.tenant_id, event_type="EvidenceReceived", aggregate_type="run",
                 aggregate_id=str(run.id), payload={"code": req.code, "snapshot": snap.code})
            received.append(req)
    session.flush()
    return received


def _coverage_problems(session: Session, run: Run, req: EvidenceRequest, snap: EvidenceSnapshot) -> list[str]:
    manifest = manifest_for(session, run).manifest
    policy = pinned_content(session, run.tenant_id, manifest, "readinessPolicy", ArtifactKind.READINESS_POLICY)[1]
    coverage = product_coverage(policy, _from_snapshot(session, snap))
    return [p for p in (req.contract or {}).get("products", []) if req.domain not in coverage.get(p, set())]


def accept(session: Session, platform: Platform, principal: Principal, run: Run, code: str) -> dict[str, Any]:
    """RECEIVED -> ACCEPTED for this request and every other request the same return satisfies; then one
    reassessment on the child snapshot with the run's pinned versions."""
    req = get(session, run, code)
    if req.status != "RECEIVED":
        raise RequestConflict(f"{req.code} is {req.status.lower().replace('_', ' ')}; nothing to accept")
    if run.state != RunState.REVIEW.value:
        raise RequestConflict(f"Evidence can be accepted while the run is in review (now {run.state})")
    snap = session.get(EvidenceSnapshot, req.received_snapshot_id)
    if snap is None or snap.status != "APPROVED":
        raise RequestConflict("The received snapshot is no longer the approved one; wait for the newer return")
    missing = _coverage_problems(session, run, req, snap)
    if missing:
        raise RequestValidation("The returned evidence does not cover the requested products",
                                [FieldError("products", f"{req.domain} still missing for {', '.join(missing)}")])
    batch = [r for r in list_requests(session, run.tenant_id, run, status=["RECEIVED"])
             if r.received_snapshot_id == snap.id and (r.id == req.id or not _coverage_problems(session, run, r, snap))]
    run.state = RunState.ANALYSING.value
    run.updated_at = _now()
    analysis = analysis_service.request_analysis(session, platform, run, principal.user_id,
                                                 reason="EVIDENCE_ACCEPTED", snapshot_id=snap.id)
    for r in batch:
        r.accepted_snapshot_id = snap.id
        r.reassessment_analysis_id = analysis.id
        r.reassessment_status = "QUEUED"
        _move(session, r, "ACCEPTED", principal.user_id, detail={"snapshot": snap.code, "analysisId": str(analysis.id),
                                                                  "via": req.code})
        emit(session, tenant_id=run.tenant_id, event_type="EvidenceAccepted", aggregate_type="run",
             aggregate_id=str(run.id), payload={"code": r.code, "snapshot": snap.code})
        _audit(session, platform, principal, "evidence_request.accepted", r,
               {"snapshot": snap.code, "analysis_id": str(analysis.id), "batch": [b.code for b in batch]})
    # other active requests for the same candidate + domain are replaced by this fresher evidence set
    for r in list_requests(session, run.tenant_id, run, status=["DRAFT", "OPEN", "IN_PROGRESS"]):
        if any(r.candidate_code == b.candidate_code and r.domain == b.domain for b in batch):
            r.status_reason = f"Replaced by {req.code} ({snap.code})"
            _move(session, r, "SUPERSEDED", "system:accept", detail={"by": req.code})
    session.commit()
    return {"accepted": [r.code for r in batch], "analysisId": str(analysis.id), "snapshot": snap.code}


def reject(session: Session, platform: Platform, principal: Principal, run: Run, code: str,
           reason: str) -> EvidenceRequest:
    """RECEIVED -> REJECTED (comment required). The same return cannot be received again; a corrected one can."""
    req = get(session, run, code)
    text = (reason or "").strip()
    if not 1 <= len(text) <= 2000:
        raise RequestValidation("A rejection needs a reason", [FieldError("reason", "Use 1-2000 characters")])
    if req.status != "RECEIVED":
        raise RequestConflict(f"{req.code} is {req.status.lower().replace('_', ' ')}; nothing to reject")
    snap = session.get(EvidenceSnapshot, req.received_snapshot_id)
    req.baseline_executions = sorted(set(req.baseline_executions) | set(_executions(snap, req.domain))) if snap \
        else req.baseline_executions
    req.received_snapshot_id = None
    req.status_reason = text
    _move(session, req, "REJECTED", principal.user_id, note=text, detail={"snapshot": snap.code if snap else None})
    emit(session, tenant_id=run.tenant_id, event_type="EvidenceRejected", aggregate_type="run",
         aggregate_id=str(run.id), payload={"code": req.code})
    _audit(session, platform, principal, "evidence_request.rejected", req, {"reason": text})
    session.commit()
    return req


def on_reassessed(session: Session, run: Run, analysis: AnalysisRun) -> None:
    """System, after a successful analysis: accepted requests of this analysis are done; waiting requests whose
    blocker no longer applies were satisfied by fresher evidence and are superseded."""
    for r in list_requests(session, run.tenant_id, run, status=["ACCEPTED"]):
        if r.reassessment_analysis_id == analysis.id:
            r.reassessment_status = "DONE"
            r.updated_at = _now()
    active_keys = {f"{b.candidate_code}|{b.rule}|{(b.facts or {}).get('key', '')}" for b in
                   _gap_blockers(session, run, None)}
    for r in list_requests(session, run.tenant_id, run, status=["DRAFT", "OPEN", "IN_PROGRESS", "RECEIVED"]):
        still = any(k.startswith(f"{r.candidate_code}|") and k.endswith(f"|{r.domain}") for k in active_keys)
        if not still:
            r.status_reason = f"Resolved by the reassessment on {analysis.seq}"
            _event(session, r, "SUPERSEDED", "system:reassessment", detail={"analysisId": str(analysis.id)})
    session.flush()
