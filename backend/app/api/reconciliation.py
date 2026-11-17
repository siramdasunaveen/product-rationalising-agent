"""Reconciliation, conflict queue, evidence snapshots and the Telco graph (step 6)."""

import uuid
from datetime import date, datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import func, select

from app.api.deps import CurrentUser, DbDep, PlatformDep, require_permission
from app.api.schemas import ErrorResponse
from app.db.models import (
    EvidenceSnapshot,
    ReconciledRecord,
    ReconciliationConflict,
    ReconciliationRun,
    SnapshotFact,
)
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import reconciliation as service

router = APIRouter(tags=["reconciliation"])

Mapper = Annotated[Principal, Depends(require_permission(Permission.EVIDENCE_MAP))]
Resolver = Annotated[Principal, Depends(require_permission(Permission.CONFLICT_RESOLVE))]
Approver = Annotated[Principal, Depends(require_permission(Permission.SNAPSHOT_APPROVE))]
ERRORS = {
    404: {"model": ErrorResponse, "description": "Run, reconciliation, conflict or snapshot not found"},
    409: {"model": ErrorResponse, "description": "conflict or snapshot_blocked (with the blockers listed)"},
    422: {"model": ErrorResponse, "description": "validation_failed with field errors"},
}


class ReconciliationOut(BaseModel):
    id: uuid.UUID
    status: str = Field(description="QUEUED | RUNNING | SUCCEEDED | FAILED")
    policy_ref: str
    canonical_ref: str
    reconciled_on: date
    inputs: list[dict]
    counts: dict | None
    summary: dict | None
    result_hash: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    inputs_current: bool = Field(description="No newer mapping apply arrived since this reconciliation")


class SnapshotOut(BaseModel):
    id: uuid.UUID
    code: str
    snapshot_no: int
    status: str = Field(description="APPROVED (current) | SUPERSEDED")
    parent_code: str | None
    policy_ref: str
    canonical_ref: str
    inputs: list[dict]
    counts: dict
    content_hash: str
    approved_by_name: str
    approved_at: datetime


class OverviewOut(BaseModel):
    run_id: uuid.UUID
    current_inputs: list[dict] = Field(description="Latest successful mapping apply of each source")
    latest: ReconciliationOut | None
    latest_succeeded: ReconciliationOut | None
    approval_blockers: list[str]
    snapshots: list[SnapshotOut]


class ConflictOut(BaseModel):
    id: uuid.UUID
    conflict_key: str
    entity: str
    key: str
    field: str
    material: bool
    status: str = Field(description="OPEN | AUTO_RESOLVED | RESOLVED | DEFERRED")
    candidates: list[dict]
    recommended_source_id: str
    reason: str
    decision: dict | None


class DecisionIn(BaseModel):
    decision: Literal["SELECT_SOURCE", "MANUAL", "DEFER"]
    source_id: str | None = None
    value: Any = None
    rationale: str | None = Field(default=None, max_length=2000)


class ApproveIn(BaseModel):
    reconciliation_id: uuid.UUID


def _rec_out(db, run, r: ReconciliationRun | None) -> ReconciliationOut | None:
    if r is None:
        return None
    return ReconciliationOut(id=r.id, status=r.status, policy_ref=r.policy_ref, canonical_ref=r.canonical_ref,
                             reconciled_on=r.reconciled_on, inputs=r.inputs, counts=r.counts, summary=r.summary,
                             result_hash=r.result_hash, error=r.error, created_at=r.created_at,
                             finished_at=r.finished_at, inputs_current=service.inputs_current(db, run, r))


def _snap_out(s: EvidenceSnapshot, codes: dict[uuid.UUID, str]) -> SnapshotOut:
    return SnapshotOut(id=s.id, code=s.code, snapshot_no=s.snapshot_no, status=s.status,
                       parent_code=codes.get(s.parent_snapshot_id) if s.parent_snapshot_id else None,
                       policy_ref=s.policy_ref, canonical_ref=s.canonical_ref, inputs=s.inputs, counts=s.counts,
                       content_hash=s.content_hash, approved_by_name=s.approved_by_name, approved_at=s.approved_at)


@router.get("/runs/{run_id}/reconciliation", response_model=OverviewOut, responses=ERRORS,
            summary="Inputs, latest reconciliation, approval blockers and snapshot history")
def overview(run_id: uuid.UUID, user: CurrentUser, db: DbDep) -> OverviewOut:
    run = service._run(db, user.tenant_id, run_id)
    latest = service.latest(db, run)
    ok = service.latest(db, run, succeeded=True)
    snaps = service.list_snapshots(db, run)
    codes = {s.id: s.code for s in snaps}
    if ok is not None:
        service._recount(db, ok)
        db.commit()
    return OverviewOut(run_id=run.id, current_inputs=service.current_inputs(db, run), latest=_rec_out(db, run, latest),
                       latest_succeeded=_rec_out(db, run, ok), approval_blockers=service.approval_blockers(db, run, ok),
                       snapshots=[_snap_out(s, codes) for s in snaps])


@router.post("/runs/{run_id}/reconciliation", response_model=ReconciliationOut, status_code=status.HTTP_202_ACCEPTED,
             responses=ERRORS, summary="Reconcile the latest applies of all sources (background job)")
def reconcile(run_id: uuid.UUID, user: Mapper, db: DbDep, platform: PlatformDep) -> ReconciliationOut:
    rec = service.request_reconciliation(db, platform, user, run_id)
    return _rec_out(db, service._run(db, user.tenant_id, run_id), rec)


@router.get("/runs/{run_id}/reconciliation/{reconciliation_id}/conflicts", response_model=list[ConflictOut],
            responses=ERRORS, summary="Conflict queue (material conflicts need a Data Steward decision)")
def conflicts(run_id: uuid.UUID, reconciliation_id: uuid.UUID, user: CurrentUser, db: DbDep,
              status_: Annotated[str | None, Query(alias="status")] = None,
              material: Annotated[bool | None, Query()] = None) -> list[ConflictOut]:
    run = service._run(db, user.tenant_id, run_id)
    rec = service.get_reconciliation(db, run, reconciliation_id)
    stmt = select(ReconciliationConflict).where(ReconciliationConflict.reconciliation_id == rec.id)
    if status_:
        stmt = stmt.where(ReconciliationConflict.status == status_)
    if material is not None:
        stmt = stmt.where(ReconciliationConflict.material.is_(material))
    rows = list(db.scalars(stmt.order_by(ReconciliationConflict.material.desc(), ReconciliationConflict.entity,
                                         ReconciliationConflict.record_key, ReconciliationConflict.field)))
    decisions = {d.id: d for d in db.scalars(select(service.StewardDecisionRow).where(
        service.StewardDecisionRow.id.in_([c.decision_id for c in rows if c.decision_id])))} if rows else {}
    out = []
    for c in rows:
        d = decisions.get(c.decision_id)
        out.append(ConflictOut(id=c.id, conflict_key=c.conflict_key, entity=c.entity, key=c.record_key, field=c.field,
                               material=c.material, status=c.status, candidates=c.candidates,
                               recommended_source_id=c.recommended_source_id, reason=c.reason,
                               decision={"decision": d.decision, "source_id": d.source_id, "value": d.value,
                                         "rationale": d.rationale, "by": d.actor_name,
                                         "at": d.created_at.isoformat()} if d else None))
    return out


@router.post("/runs/{run_id}/reconciliation/conflicts/{conflict_id}/decision", response_model=ConflictOut,
             responses=ERRORS, summary="Record a Data Steward decision (select source, manual value, defer)")
def decide(run_id: uuid.UUID, conflict_id: uuid.UUID, body: DecisionIn, user: Resolver, db: DbDep,
           platform: PlatformDep) -> ConflictOut:
    c = service.decide(db, platform, user, run_id, conflict_id, body.decision, body.source_id, body.value,
                       body.rationale)
    d = db.get(service.StewardDecisionRow, c.decision_id)
    return ConflictOut(id=c.id, conflict_key=c.conflict_key, entity=c.entity, key=c.record_key, field=c.field,
                       material=c.material, status=c.status, candidates=c.candidates,
                       recommended_source_id=c.recommended_source_id, reason=c.reason,
                       decision={"decision": d.decision, "source_id": d.source_id, "value": d.value,
                                 "rationale": d.rationale, "by": d.actor_name, "at": d.created_at.isoformat()})


@router.get("/runs/{run_id}/reconciliation/{reconciliation_id}/records", response_model=dict, responses=ERRORS,
            summary="Reconciled records with field provenance")
def records(run_id: uuid.UUID, reconciliation_id: uuid.UUID, user: CurrentUser, db: DbDep,
            entity: Annotated[str | None, Query(max_length=64)] = None,
            limit: Annotated[int, Query(ge=1, le=200)] = 50, offset: Annotated[int, Query(ge=0)] = 0) -> dict:
    run = service._run(db, user.tenant_id, run_id)
    rec = service.get_reconciliation(db, run, reconciliation_id)
    stmt = select(ReconciledRecord).where(ReconciledRecord.reconciliation_id == rec.id)
    if entity:
        stmt = stmt.where(ReconciledRecord.entity == entity)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(ReconciledRecord.entity, ReconciledRecord.record_key).limit(limit).offset(offset))
    return {"total": total, "items": [{"entity": r.entity, "key": r.record_key, "values": r.values,
                                       "provenance": r.provenance, "sources": r.sources} for r in rows]}


@router.post("/runs/{run_id}/snapshots", response_model=SnapshotOut, status_code=status.HTTP_201_CREATED,
             responses=ERRORS, summary="Approve the reconciled evidence as an immutable snapshot (server re-checks)")
def approve(run_id: uuid.UUID, body: ApproveIn, user: Approver, db: DbDep, platform: PlatformDep) -> SnapshotOut:
    snap = service.approve_snapshot(db, platform, user, run_id, body.reconciliation_id)
    run = service._run(db, user.tenant_id, run_id)
    return _snap_out(snap, {s.id: s.code for s in service.list_snapshots(db, run)})


@router.get("/runs/{run_id}/snapshots/{snapshot_id}", response_model=SnapshotOut, responses=ERRORS,
            summary="One evidence snapshot")
def get_snapshot(run_id: uuid.UUID, snapshot_id: uuid.UUID, user: CurrentUser, db: DbDep) -> SnapshotOut:
    run = service._run(db, user.tenant_id, run_id)
    snap = service.get_snapshot(db, run, snapshot_id)
    return _snap_out(snap, {s.id: s.code for s in service.list_snapshots(db, run)})


@router.get("/runs/{run_id}/snapshots/{snapshot_id}/facts", response_model=dict, responses=ERRORS,
            summary="Facts of a snapshot (one per field, with evidence ids and provenance)")
def facts(run_id: uuid.UUID, snapshot_id: uuid.UUID, user: CurrentUser, db: DbDep,
          entity: Annotated[str | None, Query(max_length=64)] = None,
          evidence_id: Annotated[str | None, Query(max_length=20)] = None,
          limit: Annotated[int, Query(ge=1, le=500)] = 100, offset: Annotated[int, Query(ge=0)] = 0) -> dict:
    run = service._run(db, user.tenant_id, run_id)
    snap = service.get_snapshot(db, run, snapshot_id)
    stmt = select(SnapshotFact).where(SnapshotFact.snapshot_id == snap.id)
    if entity:
        stmt = stmt.where(SnapshotFact.entity == entity)
    if evidence_id:
        stmt = stmt.where(SnapshotFact.evidence_id == evidence_id)
    total = db.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = db.scalars(stmt.order_by(SnapshotFact.entity, SnapshotFact.record_key, SnapshotFact.field)
                      .limit(limit).offset(offset))
    return {"total": total, "items": [{"evidence_id": f.evidence_id, "entity": f.entity, "key": f.record_key,
                                       "field": f.field, "value": f.value, "stale": f.stale,
                                       "conflicted": f.conflicted, "provenance": f.provenance} for f in rows]}


@router.get("/runs/{run_id}/snapshots/{snapshot_id}/graph", response_model=dict, responses=ERRORS,
            summary="Telco graph of a product: services, resources, workflows, billing, aggregates")
def product_graph(run_id: uuid.UUID, snapshot_id: uuid.UUID, user: CurrentUser, db: DbDep,
                  product_id: Annotated[str, Query(max_length=200)]) -> dict:
    run = service._run(db, user.tenant_id, run_id)
    return service.graph(db, run, snapshot_id, product_id)

