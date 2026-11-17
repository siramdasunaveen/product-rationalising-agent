"""Decisions and approvals (BUILD_PLAN step 14, screens 09 and 10).

Every save creates a new immutable decision version taken on the candidate's current assessment (analysis,
snapshot, manifest, recommendation) with a SHA-256 content hash. Submit re-checks decision readiness server-side
(rules 5 and 6), evaluates the policy route and creates one approval task per required scope. Approvers act on
the exact version and hash; the policy checks are re-evaluated on every read and a reassessment that materially
changes the decision supersedes the version and drafts a new one. Approval never changes a product.
"""

import uuid
from datetime import UTC, date, datetime, timedelta
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.registry import Platform
from app.db.models import (
    AnalysisRun,
    ApprovalAction,
    ApprovalTask,
    Candidate,
    Decision,
    DecisionVersion,
    EvidenceSnapshot,
    Run,
)
from app.domain import decision_readiness as dr
from app.domain import decisions as dm
from app.domain.artifacts import ArtifactKind
from app.domain.common import FieldError
from app.domain.runs import RunState
from app.ports.audit import AuditRecord
from app.ports.auth import Principal
from app.services import candidates as candidate_service
from app.services.analysis import manifest_for, pinned_content
from app.services.errors import ServiceError
from app.services.outbox import emit

APPROVAL_SLA_DAYS = 10


class DecisionError(ServiceError):
    code = "decision_error"


class DecisionNotFound(DecisionError):
    status_code = 404
    code = "not_found"


class DecisionConflict(DecisionError):
    status_code = 409
    code = "conflict"


class DecisionValidation(DecisionError):
    status_code = 422
    code = "validation_failed"


class SubmissionBlocked(DecisionError):
    status_code = 409
    code = "submission_blocked"


def _now() -> datetime:
    return datetime.now(UTC)


def _audit(session: Session, platform: Platform, principal: Principal, action: str, d: Decision,
           payload: dict[str, Any]) -> None:
    platform.audit.record(AuditRecord(
        tenant_id=principal.tenant_id, actor_id=principal.user_id, actor_roles=principal.roles,
        auth_context=principal.auth_context, action=action, entity_type="decision", entity_id=str(d.id),
        payload={"run_id": str(d.run_id), "code": d.code, **payload}), session=session)


def _settings(manifest: dict) -> dict[str, Any]:
    return {k: v["value"] for k, v in manifest["settings"].items()}


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_decision(session: Session, run: Run, ref: str) -> Decision:
    d = session.scalar(select(Decision).where(Decision.run_id == run.id, Decision.tenant_id == run.tenant_id,
                                              (Decision.code == ref.upper()) | (Decision.candidate_code == ref.upper())))
    if d is None:
        raise DecisionNotFound("Decision not found")
    return d


def for_candidate(session: Session, run: Run, candidate_code: str) -> Decision | None:
    return session.scalar(select(Decision).where(Decision.run_id == run.id, Decision.tenant_id == run.tenant_id,
                                                 Decision.candidate_code == candidate_code))


def versions(session: Session, d: Decision) -> list[DecisionVersion]:
    return list(session.scalars(select(DecisionVersion).where(DecisionVersion.decision_id == d.id)
                                .order_by(DecisionVersion.version_no)))


def tasks(session: Session, version_id: uuid.UUID) -> list[ApprovalTask]:
    return list(session.scalars(select(ApprovalTask).where(ApprovalTask.version_id == version_id)
                                .order_by(ApprovalTask.created_at)))


def actions(session: Session, d: Decision) -> list[ApprovalAction]:
    return list(session.scalars(select(ApprovalAction).where(ApprovalAction.decision_id == d.id)
                                .order_by(ApprovalAction.created_at)))


def _version_dict(v: DecisionVersion) -> dict[str, Any]:
    return {"id": str(v.id), "versionNo": v.version_no, "candidateId": str(v.candidate_id),
            "analysisId": str(v.analysis_id), "snapshotId": str(v.snapshot_id), "snapshotCode": v.snapshot_code,
            "manifestHash": v.manifest_hash, "recommendation": v.recommendation, "disposition": v.disposition,
            "recommendedAction": (v.recommendation or {}).get("action"), "targetProductId": v.target_product_id,
            "effectiveDate": v.effective_date.isoformat(), "ownerId": v.owner_id, "rationale": v.rationale,
            "commercialTreatment": v.commercial_treatment, "conditions": v.conditions, "readiness": v.readiness,
            "route": v.route, "contentHash": v.content_hash, "createdBy": v.created_by,
            "createdAt": v.created_at.isoformat(), "reason": v.reason}


def _candidate_view(session: Session, run: Run, candidate_code: str, disposition: str,
                    target: str | None) -> tuple[Candidate, dict[str, Any]]:
    c = candidate_service.resolve(session, run, candidate_code)
    readiness = candidate_service.readiness_for(session, run, c, disposition, target)
    return c, {"impact": c.impact or {}, "dimensions": c.dimensions or {}, "status": c.candidate_status,
               "recommendation": c.recommendation or {}, "readinessForDecision": readiness}


def detail(session: Session, run: Run, ref: str, principal: Principal | None = None) -> dict[str, Any]:
    d = get_decision(session, run, ref)
    manifest = manifest_for(session, run).manifest
    settings = _settings(manifest)
    names = {}
    vs = versions(session, d)
    current = next((v for v in vs if v.id == d.current_version_id), vs[-1] if vs else None)
    out: dict[str, Any] = {"id": str(d.id), "code": d.code, "runId": str(run.id), "runCode": run.run_code,
                           "candidateCode": d.candidate_code, "state": d.state, "currentVersionNo": d.current_version_no,
                           "approvedVersionId": str(d.approved_version_id) if d.approved_version_id else None,
                           "versions": [_version_dict(v) for v in vs], "current": None, "policyChecks": [],
                           "overall": None, "tasks": [], "actions": [], "myScopes": [], "canAct": False}
    if current is None:
        return out
    cv = _version_dict(current)
    try:
        c, view = _candidate_view(session, run, d.candidate_code, current.disposition, current.target_product_id)
        snap = session.get(EvidenceSnapshot, c.analysis_id and session.get(AnalysisRun, c.analysis_id).snapshot_id)
        names = candidate_service.product_names(session, snap.id, run.tenant_id) if snap else {}
        stale = c.analysis_id != current.analysis_id
        readiness = view["readinessForDecision"]
    except ServiceError:
        view, stale, readiness = {"impact": {}, "dimensions": {}}, True, None
    checks = dm.policy_checks(cv, view, settings, readiness, stale=stale)
    cv["targetName"] = names.get(current.target_product_id) if current.target_product_id else None
    cv["products"] = [{"id": s, "name": names.get(s, s)} for s in (c.subjects if names else [])]
    cv["impactSummary"] = (view.get("impact") or {}).get("summary")
    cv["impact"] = view.get("impact") or {}
    ts = tasks(session, current.id)
    out.update({"current": cv, "policyChecks": checks, "overall": dm.overall(checks), "stale": stale,
                "tasks": [{"id": str(t.id), "scope": t.scope, "state": t.state,
                           "dueDate": t.due_date.isoformat() if t.due_date else None, "actedBy": t.acted_by,
                           "actedAt": t.acted_at.isoformat() if t.acted_at else None} for t in ts],
                "actions": [{"id": str(a.id), "action": a.action, "scope": a.scope, "actorId": a.actor_id,
                             "comment": a.comment, "resultingState": a.resulting_state,
                             "contentHash": a.content_hash, "at": a.created_at.isoformat()} for a in actions(session, d)]})
    if principal is not None:
        mine = [t.scope for t in ts if t.state == "PENDING" and t.scope in principal.approval_scopes]
        out["myScopes"] = mine
        out["canAct"] = bool(mine) and d.state in ("SUBMITTED", "ON_HOLD") and principal.has("approval.act")
    return out


# ---------------------------------------------------------------------------
# Draft and submit (Decision screen)
# ---------------------------------------------------------------------------

def _next_code(session: Session, run: Run) -> str:
    codes = session.scalars(select(Decision.code).where(Decision.run_id == run.id))
    return f"DEC-{max((int(c.split('-')[1]) for c in codes), default=0) + 1:04d}"


def _form_errors(session: Session, run: Run, c: Candidate, form: dict[str, Any]) -> list[FieldError]:
    facts = (c.recommendation or {}).get("gateFacts") or {}
    eligible = dict(facts.get("eligible") or {})
    a = session.get(AnalysisRun, c.analysis_id)
    if form.get("targetProductId") and form["targetProductId"] not in eligible and a is not None:
        names = candidate_service.product_names(session, a.snapshot_id, run.tenant_id)
        if form["targetProductId"] in names:
            eligible[form["targetProductId"]] = True
    return dm.validate(form, eligible_targets=eligible, today=date.today(),
                       allowed_actions=dr.allowed_actions(c.primary_type, list(c.supporting_types)))


def save_draft(session: Session, platform: Platform, principal: Principal, run: Run, candidate_code: str,
               form: dict[str, Any], *, reason: str = "Edited") -> dict[str, Any]:
    """Create or update the decision as a NEW immutable version in DRAFT (prior versions stay read-only)."""
    c = candidate_service.resolve(session, run, candidate_code)
    errors = _form_errors(session, run, c, form)
    if errors:
        raise DecisionValidation("The decision is not valid", errors)
    d = for_candidate(session, run, c.code)
    if d is not None and d.state in ("SUBMITTED", "ON_HOLD"):
        raise DecisionConflict(f"{d.code} is in approval; return it before editing")
    if d is not None and d.state == "APPROVED":
        raise DecisionConflict(f"{d.code} is approved; it can only change through a reassessment")
    if d is None:
        d = Decision(tenant_id=run.tenant_id, run_id=run.id, code=_next_code(session, run), candidate_code=c.code,
                     state="DRAFT", current_version_no=0)
        session.add(d)
        session.flush()
    a = session.get(AnalysisRun, c.analysis_id)
    snap = session.get(EvidenceSnapshot, a.snapshot_id)
    m = manifest_for(session, run)
    rec = {k: v for k, v in (c.recommendation or {}).items() if k not in ("gateFacts", "decisionReadiness")}
    conditions = list(rec.get("conditions") or []) if form["disposition"] == rec.get("action") else []
    treatment = form.get("commercialTreatment") or next(
        (dm.COMMERCIAL_TREATMENT[x] for x in conditions if x in dm.COMMERCIAL_TREATMENT), None)
    readiness = candidate_service.readiness_for(session, run, c, form["disposition"], form.get("targetProductId"))
    no = d.current_version_no + 1
    content = {"decision": d.code, "versionNo": no, "candidate": c.code, "analysisId": str(c.analysis_id),
               "snapshot": snap.code, "manifestHash": m.manifest_hash, "recommendation": rec,
               "disposition": form["disposition"], "targetProductId": form.get("targetProductId"),
               "effectiveDate": form["effectiveDate"].isoformat(), "ownerId": form["ownerId"].strip(),
               "rationale": form["rationale"].strip(), "commercialTreatment": treatment, "conditions": conditions}
    v = DecisionVersion(tenant_id=run.tenant_id, decision_id=d.id, version_no=no, candidate_id=c.id,
                        analysis_id=c.analysis_id, snapshot_id=snap.id, snapshot_code=snap.code,
                        manifest_hash=m.manifest_hash, recommendation=rec, disposition=form["disposition"],
                        target_product_id=form.get("targetProductId"), effective_date=form["effectiveDate"],
                        owner_id=form["ownerId"].strip(), rationale=form["rationale"].strip(),
                        commercial_treatment=treatment, conditions=conditions, readiness=readiness, route=None,
                        content_hash=dm.content_hash(content), created_by=principal.user_id, reason=reason)
    session.add(v)
    session.flush()
    d.current_version_id, d.current_version_no, d.state, d.updated_at = v.id, no, "DRAFT", _now()
    _sync_candidate(session, c, "DRAFT")
    emit(session, tenant_id=run.tenant_id, event_type="DecisionVersionCreated", aggregate_type="run",
         aggregate_id=str(run.id), payload={"code": d.code, "version": no, "candidate": c.code,
                                            "contentHash": v.content_hash})
    _audit(session, platform, principal, "decision.version_created", d,
           {"version": no, "content_hash": v.content_hash, "disposition": v.disposition})
    session.commit()
    return detail(session, run, d.code, principal)


def _sync_candidate(session: Session, c: Candidate, decision_state: str | None) -> None:
    """Candidate Status follows the decision (precedence BLOCKED > EVIDENCE_GAP > APPROVAL > DECISION > REVIEW)."""
    from app.domain.assessment import candidate_status
    from app.services.assessment import ACTIVE
    from app.db.models import Blocker

    active = [{"category": b.category, "consequence": b.consequence} for b in session.scalars(
        select(Blocker).where(Blocker.run_id == c.run_id, Blocker.candidate_code == c.code, Blocker.state.in_(ACTIVE),
                              Blocker.applicable.is_(True), Blocker.superseded.is_(False)))]
    mapped = {"DRAFT": "DRAFT", "RETURNED": "DRAFT", "SUBMITTED": "SUBMITTED", "ON_HOLD": "IN_APPROVAL",
              "APPROVED": "APPROVED"}.get(decision_state or "")
    c.candidate_status = candidate_status(active, c.guardrail or "NOT_ASSESSED", mapped)


def submit(session: Session, platform: Platform, principal: Principal, run: Run, candidate_code: str,
           version_id: str, expected_hash: str | None) -> dict[str, Any]:
    """Freeze the version, gate the submission (rules 5/6, execution gate) and create the approval route."""
    c = candidate_service.resolve(session, run, candidate_code)
    d = for_candidate(session, run, c.code)
    if d is None or d.current_version_id is None:
        raise DecisionNotFound("Save a decision first")
    if d.state not in ("DRAFT", "RETURNED"):
        raise DecisionConflict(f"{d.code} is {d.state.lower().replace('_', ' ')}")
    v = session.get(DecisionVersion, uuid.UUID(version_id))
    if v is None or v.decision_id != d.id or v.id != d.current_version_id:
        raise DecisionConflict("Submit the current version; reload the decision")
    if expected_hash and expected_hash != v.content_hash:
        raise DecisionConflict("The decision changed since you loaded it; reload before submitting")
    if v.analysis_id != c.analysis_id:
        raise DecisionConflict("The candidate was reassessed after this version; save a new version first")
    manifest = manifest_for(session, run).manifest
    settings = _settings(manifest)
    readiness = candidate_service.readiness_for(session, run, c, v.disposition, v.target_product_id)
    blockers = dm.submission_blockers(readiness, settings, c.dimensions or {}, v.disposition)
    if blockers:
        raise SubmissionBlocked("The decision cannot be submitted yet", [FieldError("submission", b) for b in blockers])
    policy = pinned_content(session, run.tenant_id, manifest, "policyPack", ArtifactKind.POLICY_PACK)
    priority = pinned_content(session, run.tenant_id, manifest, "priorityPolicy", ArtifactKind.PRIORITY_POLICY)[1]
    route = dm.approval_route({**policy[1], "_ref": policy[0]}, v.disposition, c.impact or {},
                              priority.get("overrides", []))
    for t in tasks(session, v.id):
        t.state, t.updated_at = "CANCELLED", _now()
    due = date.today() + timedelta(days=APPROVAL_SLA_DAYS)
    for scope in route["scopes"]:
        session.add(ApprovalTask(tenant_id=run.tenant_id, run_id=run.id, decision_id=d.id, version_id=v.id,
                                 scope=scope, state="PENDING", due_date=due))
    # the route is recorded with the version through the action row (the version row itself is immutable)
    session.add(ApprovalAction(tenant_id=run.tenant_id, run_id=run.id, decision_id=d.id, version_id=v.id,
                               action="SUBMIT", actor_id=principal.user_id, actor_roles=list(principal.roles),
                               auth_context=dict(principal.auth_context), content_hash=v.content_hash,
                               policy_result={"readiness": readiness, "route": route}, comment=None,
                               resulting_state="SUBMITTED"))
    d.state, d.updated_at = "SUBMITTED", _now()
    _sync_candidate(session, c, "SUBMITTED")
    if run.state == RunState.REVIEW.value:
        run.state, run.updated_at = RunState.APPROVAL.value, _now()
    emit(session, tenant_id=run.tenant_id, event_type="ApprovalTasksCreated", aggregate_type="run",
         aggregate_id=str(run.id), payload={"code": d.code, "version": v.version_no, "scopes": route["scopes"]})
    _audit(session, platform, principal, "decision.submitted", d,
           {"version": v.version_no, "content_hash": v.content_hash, "route": route})
    session.commit()
    return detail(session, run, d.code, principal)


# ---------------------------------------------------------------------------
# Approval (Approval screen)
# ---------------------------------------------------------------------------

def act(session: Session, platform: Platform, principal: Principal, run: Run, ref: str, action: str,
        comment: str | None, expected_hash: str | None) -> dict[str, Any]:
    action = (action or "").upper()
    d = get_decision(session, run, ref)
    if d.state not in ("SUBMITTED", "ON_HOLD"):
        raise DecisionConflict(f"{d.code} is {d.state.lower().replace('_', ' ')}; nothing to approve")
    v = session.get(DecisionVersion, d.current_version_id)
    ts = tasks(session, v.id)
    mine = [t for t in ts if t.scope in principal.approval_scopes and t.state != "CANCELLED"]
    pending_mine = [t for t in mine if t.state in ("PENDING", "ON_HOLD")]
    view = detail(session, run, d.code, principal)
    policy = pinned_content(session, run.tenant_id, manifest_for(session, run).manifest, "policyPack",
                            ArtifactKind.POLICY_PACK)[1]
    submitter = next((a.actor_id for a in actions(session, d) if a.action == "SUBMIT" and a.version_id == v.id), None)
    problems = dm.validate_action(action, comment, scope_pending=bool(pending_mine), has_scope=bool(mine),
                                  self_approval=principal.user_id in (v.owner_id, v.created_by, submitter),
                                  allow_self_approval=bool(policy.get("allowSelfApproval", False)),
                                  blocked=view["overall"] == "BLOCKED", expected_hash=expected_hash,
                                  actual_hash=v.content_hash)
    if problems:
        raise DecisionValidation("The approval action is not allowed", [FieldError("action", p) for p in problems])
    task = pending_mine[0]
    task_state = {"APPROVE": "APPROVED", "REJECT": "REJECTED", "RETURN": "RETURNED", "HOLD": "ON_HOLD"}[action]
    task.state, task.acted_by, task.acted_at, task.updated_at = task_state, principal.user_id, _now(), _now()
    if action in ("REJECT", "RETURN"):
        for t in ts:
            if t.state in ("PENDING", "ON_HOLD") and t.id != task.id:
                t.state, t.updated_at = "CANCELLED", _now()
    new_state = dm.next_state(d.state, action, [{"scope": t.scope, "state": t.state} for t in ts
                                                 if t.state != "CANCELLED"])
    session.add(ApprovalAction(tenant_id=run.tenant_id, run_id=run.id, decision_id=d.id, version_id=v.id,
                               task_id=task.id, scope=task.scope, action=action, actor_id=principal.user_id,
                               actor_roles=list(principal.roles), auth_context=dict(principal.auth_context),
                               content_hash=v.content_hash,
                               policy_result={"checks": view["policyChecks"], "overall": view["overall"]},
                               comment=(comment or "").strip() or None, resulting_state=new_state))
    d.state, d.updated_at = new_state, _now()
    if new_state == "APPROVED":
        d.approved_version_id = v.id
    c = candidate_service.resolve(session, run, d.candidate_code)
    _sync_candidate(session, c, new_state)
    event = {"APPROVE": "DecisionApproved" if new_state == "APPROVED" else "ApprovalRecorded",
             "REJECT": "DecisionRejected", "RETURN": "DecisionReturned", "HOLD": "DecisionHeld"}[action]
    emit(session, tenant_id=run.tenant_id, event_type=event, aggregate_type="run", aggregate_id=str(run.id),
         payload={"code": d.code, "version": v.version_no, "scope": task.scope, "state": new_state})
    _audit(session, platform, principal, f"decision.{action.lower()}", d,
           {"version": v.version_no, "content_hash": v.content_hash, "scope": task.scope, "state": new_state,
            "policy_result": view["overall"], "comment": (comment or "").strip() or None})
    session.commit()
    return detail(session, run, d.code, principal)


def queue(session: Session, principal: Principal, *, run: Run | None = None, mine: bool = True) -> list[dict[str, Any]]:
    """Approval queue: decisions in approval with a pending task in one of my scopes (or all, for viewers)."""
    stmt = select(Decision).where(Decision.tenant_id == principal.tenant_id, Decision.state.in_(("SUBMITTED", "ON_HOLD")))
    if run is not None:
        stmt = stmt.where(Decision.run_id == run.id)
    out = []
    for d in session.scalars(stmt.order_by(Decision.updated_at)):
        r = session.get(Run, d.run_id)
        ts = tasks(session, d.current_version_id)
        pending = [t for t in ts if t.state in ("PENDING", "ON_HOLD")]
        if mine and not any(t.scope in principal.approval_scopes for t in pending):
            continue
        v = session.get(DecisionVersion, d.current_version_id)
        out.append({"code": d.code, "runId": str(d.run_id), "runCode": r.run_code, "candidateCode": d.candidate_code,
                    "state": d.state, "disposition": v.disposition, "targetProductId": v.target_product_id,
                    "versionNo": v.version_no, "effectiveDate": v.effective_date.isoformat(),
                    "pendingScopes": [t.scope for t in pending],
                    "dueDate": min((t.due_date for t in pending if t.due_date), default=None).isoformat()
                    if any(t.due_date for t in pending) else None})
    return out


# ---------------------------------------------------------------------------
# Reassessment: material change -> new version and route
# ---------------------------------------------------------------------------

def on_reassessed(session: Session, run: Run, analysis: AnalysisRun) -> None:
    for d in session.scalars(select(Decision).where(Decision.run_id == run.id, Decision.tenant_id == run.tenant_id,
                                                    Decision.state.in_(tuple(dm.OPEN_STATES | {"APPROVED"})))):
        v = session.get(DecisionVersion, d.current_version_id) if d.current_version_id else None
        if v is None or v.analysis_id == analysis.id:
            continue
        try:
            c, view = _candidate_view(session, run, d.candidate_code, v.disposition, v.target_product_id)
        except ServiceError:
            continue
        reasons = dm.material_change(_version_dict(v), view)
        if not reasons:
            _sync_candidate(session, c, d.state)
            continue
        for t in tasks(session, v.id):
            if t.state in ("PENDING", "ON_HOLD"):
                t.state, t.updated_at = "CANCELLED", _now()
        session.add(ApprovalAction(tenant_id=run.tenant_id, run_id=run.id, decision_id=d.id, version_id=v.id,
                                   action="SUPERSEDE", actor_id="system:reassessment", actor_roles=[],
                                   auth_context={}, content_hash=v.content_hash,
                                   policy_result={"reasons": reasons, "analysisId": str(analysis.id)},
                                   comment="; ".join(reasons), resulting_state="SUPERSEDED"))
        previous_state = d.state
        d.state, d.updated_at = "SUPERSEDED", _now()
        emit(session, tenant_id=run.tenant_id, event_type="DecisionSuperseded", aggregate_type="run",
             aggregate_id=str(run.id), payload={"code": d.code, "version": v.version_no, "reasons": reasons,
                                                "previousState": previous_state})
        _sync_candidate(session, c, None)
    session.flush()
