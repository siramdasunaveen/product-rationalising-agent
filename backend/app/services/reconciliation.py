"""Reconciliation and evidence snapshots (step 6).

1. ``request_reconciliation``: take the latest successful mapping apply of every source of the run and queue a
   background reconciliation pinned to the published precedence policy and canonical schema.
2. ``run_reconciliation`` (job): reconcile field by field (``app.domain.reconcile``), store the working set:
   records with provenance, relationships, conflicts, stale sources, dangling links, possible duplicates.
3. ``decide``: a Data Steward settles a conflict (select source / manual value / defer). Decisions are append-only
   and are re-applied by later reconciliations while the conflicting values are unchanged.
4. ``approve_snapshot``: after a server re-check (current data, no open material conflict) the working set is frozen
   into an immutable snapshot (SNAP-001 ...) with one fact per field, stable evidence ids and a content hash.
5. ``graph``: walk a product's Telco graph in a snapshot (services, resources, workflows, billing, aggregates).
"""

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, insert, select, type_coerce
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Session

from app.adapters.registry import Platform
from app.db.models import (
    EvidenceSnapshot,
    EvidenceSource,
    EvidenceUpload,
    FieldLineage,
    MappingExecution,
    ReconciledRecord,
    ReconciledRelationship,
    ReconciliationConflict,
    ReconciliationRun,
    Run,
    SnapshotFact,
    SnapshotRelationship,
    StagedRecord,
    StagedRelationship,
    StewardDecisionRow,
)
from app.domain import reconcile as rc
from app.domain.artifact_content import find_field
from app.domain.artifacts import ArtifactKind, format_ref, parse_ref
from app.domain.common import FieldError
from app.domain.mapping import MappingValueError, coerce, to_json
from app.ports.audit import AuditRecord
from app.ports.auth import Principal
from app.seed.telco import PRECEDENCE_KEY
from app.services import artifacts as registry
from app.services.errors import ServiceError
from app.services.evidence import EDITABLE_STATES, selected_domains
from app.services.outbox import emit

RECONCILE_JOB = "app.jobs.reconciliation.reconcile_run"
RATIONALE_MIN, RATIONALE_MAX = 3, 2000
MAX_DANGLING_LISTED = 500


class ReconciliationError(ServiceError):
    code = "reconciliation_error"


class ReconciliationNotFound(ReconciliationError):
    status_code = 404
    code = "not_found"


class ReconciliationConflictError(ReconciliationError):
    status_code = 409
    code = "conflict"


class ReconciliationValidationError(ReconciliationError):
    status_code = 422
    code = "validation_failed"


class SnapshotBlocked(ReconciliationError):
    status_code = 409
    code = "snapshot_blocked"


def _now() -> datetime:
    return datetime.now(UTC)


def _run(session: Session, tenant_id: str, run_id: uuid.UUID) -> Run:
    run = session.scalar(select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id))
    if run is None:
        raise ReconciliationNotFound("Run not found")
    return run


def _audit_event(session: Session, platform: Platform, principal: Principal | None, tenant_id: str, action: str,
                 event: str, entity_type: str, entity_id: str, payload: dict[str, Any]) -> None:
    platform.audit.record(AuditRecord(
        tenant_id=tenant_id, actor_id=principal.user_id if principal else "system:reconciliation",
        actor_roles=principal.roles if principal else (), auth_context=principal.auth_context if principal else {},
        action=action, entity_type=entity_type, entity_id=entity_id, payload=payload), session=session)
    emit(session, tenant_id=tenant_id, event_type=event, aggregate_type=entity_type, aggregate_id=entity_id,
         payload=payload)


# ---------------------------------------------------------------------------
# Inputs: the latest successful apply of every source of the run
# ---------------------------------------------------------------------------

def current_inputs(session: Session, run: Run) -> list[dict[str, Any]]:
    domains = {d.value for d in selected_domains(run)}
    out = []
    sources = session.scalars(select(EvidenceSource).where(EvidenceSource.run_id == run.id,
                                                           EvidenceSource.tenant_id == run.tenant_id)
                              .order_by(EvidenceSource.domain, EvidenceSource.created_at))
    for src in sources:
        if src.domain not in domains:
            continue
        row = session.execute(
            select(MappingExecution, EvidenceUpload)
            .join(EvidenceUpload, EvidenceUpload.id == MappingExecution.upload_id)
            .where(EvidenceUpload.source_id == src.id, MappingExecution.status == "SUCCEEDED",
                   MappingExecution.tenant_id == run.tenant_id)
            .order_by(MappingExecution.finished_at.desc()).limit(1)).first()
        if row is None:
            continue
        ex, upload = row
        as_of = upload.as_of_date or (upload.confirmed_at or upload.created_at).date()
        out.append({"source_id": str(src.id), "source_name": src.name, "domain": src.domain, "system": src.system,
                    "execution_id": str(ex.id), "output_hash": ex.output_hash,
                    "mapping_version_id": str(ex.mapping_version_id), "upload_id": str(upload.id),
                    "filename": upload.filename, "as_of": as_of.isoformat(),
                    "records": sum((ex.counts or {}).get("records", {}).values())})
    return out


def _resolve_policy(session: Session, tenant_id: str):
    try:
        policy_art, policy_v = registry.resolve(session, tenant_id, PRECEDENCE_KEY, kind=ArtifactKind.PRECEDENCE_POLICY)
        canon_key = policy_v.content["canonicalSchema"]
        canon_art, canon_v = registry.resolve(session, tenant_id, canon_key, kind=ArtifactKind.CANONICAL_SCHEMA)
    except registry.ArtifactNotFound as exc:
        raise ReconciliationConflictError(f"Publish the precedence policy and canonical schema first ({exc})") from exc
    return (format_ref(policy_art.key, registry.version_of(policy_v)), policy_v,
            format_ref(canon_art.key, registry.version_of(canon_v)))


def request_reconciliation(session: Session, platform: Platform, principal: Principal,
                           run_id: uuid.UUID) -> ReconciliationRun:
    run = _run(session, principal.tenant_id, run_id)
    if run.state not in EDITABLE_STATES:
        raise ReconciliationConflictError(f"Evidence can only be reconciled while the run is configuring "
                                          f"(now {run.state})")
    inputs = current_inputs(session, run)
    if not inputs:
        raise ReconciliationConflictError("Apply at least one published mapping first",
                                          [FieldError("inputs", "No staged records yet")])
    policy_ref, policy_v, canonical_ref = _resolve_policy(session, principal.tenant_id)
    rec = ReconciliationRun(tenant_id=principal.tenant_id, run_id=run.id, status="QUEUED", policy_ref=policy_ref,
                            policy_digest=policy_v.digest, canonical_ref=canonical_ref,
                            reconciled_on=_now().date(), inputs=inputs, requested_by=principal.user_id)
    session.add(rec)
    session.flush()
    _audit_event(session, platform, principal, principal.tenant_id, "reconciliation.requested",
                 "ReconciliationRequested", "run", str(run.id),
                 {"reconciliation_id": str(rec.id), "policy": policy_ref, "canonical": canonical_ref,
                  "inputs": [{k: i[k] for k in ("source_name", "domain", "execution_id", "as_of")} for i in inputs]})
    session.commit()
    rec.job_id = platform.jobs.enqueue(RECONCILE_JOB, str(rec.id))
    session.commit()
    return rec


def _source_data(session: Session, tenant_id: str, inp: dict[str, Any]) -> rc.SourceData:
    ex_id = uuid.UUID(inp["execution_id"])
    info = rc.SourceInfo(source_id=inp["source_id"], name=inp["source_name"], domain=inp["domain"],
                         system=inp["system"], as_of=date.fromisoformat(inp["as_of"]), execution_id=inp["execution_id"])
    lineage: dict[uuid.UUID, dict[str, int]] = defaultdict(dict)
    for rid, field_name, lid in session.execute(
            select(FieldLineage.staged_record_id, FieldLineage.canonical_field, FieldLineage.id)
            .where(FieldLineage.execution_id == ex_id, FieldLineage.tenant_id == tenant_id)):
        lineage[rid][field_name] = lid
    records = [rc.SourceRecord(r.entity, r.record_key, dict(r.values), lineage.get(r.id, {}))
               for r in session.scalars(select(StagedRecord).where(StagedRecord.execution_id == ex_id,
                                                                   StagedRecord.tenant_id == tenant_id))]
    edges = [rc.SourceEdge(e.name, e.from_entity, e.from_key, e.to_entity, e.to_key)
             for e in session.scalars(select(StagedRelationship).where(StagedRelationship.execution_id == ex_id,
                                                                       StagedRelationship.tenant_id == tenant_id))]
    return rc.SourceData(info, records, edges)


def _decisions(session: Session, run_id: uuid.UUID, tenant_id: str) -> dict[tuple[str, str], rc.StewardDecision]:
    out: dict[tuple[str, str], rc.StewardDecision] = {}
    for d in session.scalars(select(StewardDecisionRow).where(StewardDecisionRow.run_id == run_id,
                                                              StewardDecisionRow.tenant_id == tenant_id)
                             .order_by(StewardDecisionRow.created_at)):  # latest decision wins
        out[(d.conflict_key, d.fingerprint)] = rc.StewardDecision(d.decision, str(d.id), d.source_id, d.value,
                                                                  d.rationale)
    return out


def _content(session: Session, tenant_id: str, ref: str) -> dict:
    key, ver = parse_ref(ref)
    return registry.resolve(session, tenant_id, key, ver)[1].content


def run_reconciliation(session: Session, platform: Platform, reconciliation_id: uuid.UUID) -> ReconciliationRun:
    rec = session.scalar(select(ReconciliationRun).where(ReconciliationRun.id == reconciliation_id).with_for_update())
    if rec is None:
        raise ReconciliationNotFound("Reconciliation not found")
    if rec.status != "QUEUED":
        session.commit()
        return rec
    rec.status = "RUNNING"
    session.commit()
    try:
        sources = [_source_data(session, rec.tenant_id, i) for i in rec.inputs]
        policy = rc.Policy(_content(session, rec.tenant_id, rec.policy_ref))
        canonical = _content(session, rec.tenant_id, rec.canonical_ref)
        result = rc.reconcile(sources, policy, canonical, rec.reconciled_on,
                              _decisions(session, rec.run_id, rec.tenant_id))
        _store(session, rec, result)
    except Exception as exc:  # noqa: BLE001 - record the failure; the user can reconcile again
        session.rollback()
        rec = session.get(ReconciliationRun, reconciliation_id)
        rec.status, rec.error, rec.finished_at = "FAILED", f"{type(exc).__name__}: {exc}"[:2000], _now()
        session.commit()
        return rec
    rec.status, rec.finished_at = "SUCCEEDED", _now()
    rec.counts = result.counts()
    rec.summary = {"stale_sources": result.stale_sources, "dangling": result.dangling[:MAX_DANGLING_LISTED],
                   "dangling_total": len(result.dangling), "possible_duplicates": result.possible_duplicates}
    rec.result_hash = rc.result_hash(result)
    _audit_event(session, platform, None, rec.tenant_id, "reconciliation.completed", "ReconciliationCompleted",
                 "run", str(rec.run_id), {"reconciliation_id": str(rec.id), "counts": rec.counts,
                                          "result_hash": rec.result_hash})
    session.commit()
    return rec


def _store(session: Session, rec: ReconciliationRun, result: rc.Result) -> None:
    rows = [{"id": uuid.uuid4(), "tenant_id": rec.tenant_id, "reconciliation_id": rec.id, "entity": r.entity,
             "record_key": r.key, "values": r.values, "provenance": r.provenance, "sources": r.sources}
            for _, r in sorted(result.records.items())]
    if rows:
        session.execute(insert(ReconciledRecord), rows)
    edges = [{"tenant_id": rec.tenant_id, "reconciliation_id": rec.id, "name": e.name, "from_entity": e.from_entity,
              "from_key": e.from_key, "to_entity": e.to_entity, "to_key": e.to_key, "sources": e.sources}
             for e in result.edges]
    if edges:
        session.execute(insert(ReconciledRelationship), edges)
    conflicts = [{"id": uuid.uuid4(), "tenant_id": rec.tenant_id, "reconciliation_id": rec.id,
                  "conflict_key": c.conflict_key, "entity": c.entity, "record_key": c.key, "field": c.field,
                  "material": c.material, "fingerprint": c.fingerprint, "candidates": c.candidates,
                  "recommended_source_id": c.recommended_source_id, "reason": c.reason, "status": c.status,
                  "decision_id": uuid.UUID(c.decision_id) if c.decision_id else None} for c in result.conflicts]
    if conflicts:
        session.execute(insert(ReconciliationConflict), conflicts)


def latest(session: Session, run: Run, *, succeeded: bool = False) -> ReconciliationRun | None:
    stmt = select(ReconciliationRun).where(ReconciliationRun.run_id == run.id,
                                           ReconciliationRun.tenant_id == run.tenant_id)
    if succeeded:
        stmt = stmt.where(ReconciliationRun.status == "SUCCEEDED")
    return session.scalar(stmt.order_by(ReconciliationRun.created_at.desc()).limit(1))


def get_reconciliation(session: Session, run: Run, reconciliation_id: uuid.UUID) -> ReconciliationRun:
    rec = session.scalar(select(ReconciliationRun).where(ReconciliationRun.id == reconciliation_id,
                                                         ReconciliationRun.run_id == run.id,
                                                         ReconciliationRun.tenant_id == run.tenant_id))
    if rec is None:
        raise ReconciliationNotFound("Reconciliation not found")
    return rec


def inputs_current(session: Session, run: Run, rec: ReconciliationRun) -> bool:
    now = {i["source_id"]: i["execution_id"] for i in current_inputs(session, run)}
    then = {i["source_id"]: i["execution_id"] for i in rec.inputs}
    return now == then


# ---------------------------------------------------------------------------
# Data Steward decisions
# ---------------------------------------------------------------------------

def _recount(session: Session, rec: ReconciliationRun) -> None:
    by_status = dict(session.execute(
        select(ReconciliationConflict.status, func.count()).where(ReconciliationConflict.reconciliation_id == rec.id)
        .group_by(ReconciliationConflict.status)).all())
    open_material = session.scalar(select(func.count()).select_from(ReconciliationConflict).where(
        ReconciliationConflict.reconciliation_id == rec.id, ReconciliationConflict.status == rc.ConflictStatus.OPEN,
        ReconciliationConflict.material.is_(True))) or 0
    counts = dict(rec.counts or {})
    counts["conflicts"] = {s: by_status.get(s, 0) for s in (rc.ConflictStatus.OPEN, rc.ConflictStatus.AUTO_RESOLVED,
                                                            rc.ConflictStatus.RESOLVED, rc.ConflictStatus.DEFERRED)}
    counts["open_material_conflicts"] = open_material
    rec.counts = counts


def decide(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID, conflict_id: uuid.UUID,
           decision: str, source_id: str | None, value: Any, rationale: str | None) -> ReconciliationConflict:
    run = _run(session, principal.tenant_id, run_id)
    if run.state not in EDITABLE_STATES:
        raise ReconciliationConflictError(f"Evidence can only be changed while the run is configuring (now {run.state})")
    conflict = session.scalar(select(ReconciliationConflict).where(
        ReconciliationConflict.id == conflict_id, ReconciliationConflict.tenant_id == principal.tenant_id
    ).with_for_update())
    rec = session.get(ReconciliationRun, conflict.reconciliation_id) if conflict else None
    if conflict is None or rec.run_id != run.id:
        raise ReconciliationNotFound("Conflict not found")
    current = latest(session, run, succeeded=True)
    if current is None or current.id != rec.id:
        raise ReconciliationConflictError("A newer reconciliation exists; decide on its conflicts")
    if decision not in rc.DECISIONS:
        raise ReconciliationValidationError("Unknown decision",
                                            [FieldError("decision", "SELECT_SOURCE, MANUAL or DEFER")])
    rationale = " ".join((rationale or "").split()) or None
    errors: list[FieldError] = []
    candidate_ids = {c["source_id"] for c in conflict.candidates}
    canonical = _content(session, rec.tenant_id, rec.canonical_ref)
    stored_value: Any = None
    if decision == rc.Decision.SELECT_SOURCE and source_id not in candidate_ids:
        errors.append(FieldError("source_id", "Choose one of the sources in this conflict"))
    if decision in (rc.Decision.MANUAL, rc.Decision.DEFER) and not (
            rationale and RATIONALE_MIN <= len(rationale) <= RATIONALE_MAX):
        errors.append(FieldError("rationale", f"Explain the decision ({RATIONALE_MIN}-{RATIONALE_MAX} characters)"))
    if decision == rc.Decision.MANUAL:
        fdef = find_field(canonical, conflict.entity, conflict.field)
        try:
            stored_value = to_json(coerce(value, fdef)) if fdef else None
        except MappingValueError as exc:
            errors.append(FieldError("value", str(exc)))
        if stored_value is None and not errors:
            errors.append(FieldError("value", "Enter the value to use"))
    if errors:
        raise ReconciliationValidationError("The decision is not valid", errors)

    row = StewardDecisionRow(tenant_id=principal.tenant_id, run_id=run.id, conflict_key=conflict.conflict_key,
                             fingerprint=conflict.fingerprint, entity=conflict.entity, record_key=conflict.record_key,
                             field=conflict.field, decision=decision,
                             source_id=source_id if decision == rc.Decision.SELECT_SOURCE else None,
                             value=stored_value, rationale=rationale, actor_id=principal.user_id,
                             actor_name=principal.name)
    session.add(row)
    session.flush()

    record = session.scalar(select(ReconciledRecord).where(
        ReconciledRecord.reconciliation_id == rec.id, ReconciledRecord.entity == conflict.entity,
        ReconciledRecord.record_key == conflict.record_key).with_for_update())
    values, provenance = dict(record.values), dict(record.provenance)
    prov = dict(provenance[conflict.field])
    if decision == rc.Decision.SELECT_SOURCE:
        values[conflict.field] = next(c["value"] for c in conflict.candidates if c["source_id"] == source_id)
        prov.update(rule="STEWARD_SELECTED", chosen_source=source_id, sources=[source_id], conflicted=False,
                    stale=next(c["stale"] for c in conflict.candidates if c["source_id"] == source_id))
        conflict.status = rc.ConflictStatus.RESOLVED
    elif decision == rc.Decision.MANUAL:
        values[conflict.field] = stored_value
        prov.update(rule="STEWARD_MANUAL", chosen_source=None, conflicted=False)
        conflict.status = rc.ConflictStatus.RESOLVED
    else:
        values.pop(conflict.field, None)
        prov.update(rule="DEFERRED", chosen_source=None, conflicted=True)
        conflict.status = rc.ConflictStatus.DEFERRED
    prov["decision_id"] = str(row.id)
    provenance[conflict.field] = prov
    record.values, record.provenance = values, provenance
    conflict.decision_id = row.id
    session.flush()
    _recount(session, rec)
    _audit_event(session, platform, principal, principal.tenant_id, "reconciliation.decision_recorded",
                 "DataStewardDecisionRecorded", "run", str(run.id), {
                     "conflict_key": conflict.conflict_key, "entity": conflict.entity, "key": conflict.record_key,
                     "field": conflict.field, "decision": decision, "source_id": row.source_id,
                     "value": stored_value, "rationale": rationale, "values_fingerprint": conflict.fingerprint})
    session.commit()
    return conflict


# ---------------------------------------------------------------------------
# Snapshot approval
# ---------------------------------------------------------------------------

def approval_blockers(session: Session, run: Run, rec: ReconciliationRun | None) -> list[str]:
    if rec is None or rec.status != "SUCCEEDED":
        return ["Run a reconciliation first"]
    blockers = []
    current = latest(session, run, succeeded=True)
    if current is None or current.id != rec.id:
        blockers.append("A newer reconciliation exists")
    if not inputs_current(session, run, rec):
        blockers.append("New mapping applies arrived since this reconciliation; reconcile again")
    approved = session.scalar(select(EvidenceSnapshot.code).where(EvidenceSnapshot.reconciliation_id == rec.id))
    if approved:
        blockers.append(f"This reconciliation is already approved as {approved}; reconcile again after new data")
    open_material = (rec.counts or {}).get("open_material_conflicts", 0)
    if open_material:
        blockers.append(f"{open_material} material conflict(s) still need a Data Steward decision")
    if run.state not in EDITABLE_STATES:
        blockers.append(f"The run is {run.state}; evidence is locked")
    return blockers


def approve_snapshot(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID,
                     reconciliation_id: uuid.UUID) -> EvidenceSnapshot:
    run = _run(session, principal.tenant_id, run_id)
    session.execute(select(Run.id).where(Run.id == run.id).with_for_update())  # one approval at a time per run
    rec = get_reconciliation(session, run, reconciliation_id)
    _recount(session, rec)
    blockers = approval_blockers(session, run, rec)
    if blockers:
        raise SnapshotBlocked("The snapshot cannot be approved yet", [FieldError("snapshot", b) for b in blockers])

    records = list(session.scalars(select(ReconciledRecord).where(ReconciledRecord.reconciliation_id == rec.id)))
    edges = list(session.scalars(select(ReconciledRelationship).where(
        ReconciledRelationship.reconciliation_id == rec.id)))
    result = rc.Result(
        records={(r.entity, r.record_key): rc.ReconciledRecord(r.entity, r.record_key, r.values, r.provenance,
                                                               r.sources) for r in records},
        edges=[rc.ReconciledEdge(e.name, e.from_entity, e.from_key, e.to_entity, e.to_key, e.sources) for e in edges])
    facts, rels = rc.snapshot_facts(result)
    content_hash = rc.snapshot_content_hash(facts, rels)

    previous = session.scalar(select(EvidenceSnapshot).where(EvidenceSnapshot.run_id == run.id,
                                                             EvidenceSnapshot.status == "APPROVED").with_for_update())
    number = (session.scalar(select(func.max(EvidenceSnapshot.snapshot_no)).where(
        EvidenceSnapshot.run_id == run.id)) or 0) + 1
    if previous is not None:
        previous.status = "SUPERSEDED"
        session.flush()
    counts = {"facts": len(facts), "relationships": len(rels),
              "records": (rec.counts or {}).get("records", {}),
              "stale_facts": sum(1 for f in facts if f["stale"]),
              "conflicted_facts": sum(1 for f in facts if f["conflicted"]),
              "conflicts": (rec.counts or {}).get("conflicts", {})}
    snap = EvidenceSnapshot(tenant_id=run.tenant_id, run_id=run.id, snapshot_no=number, code=f"SNAP-{number:03d}",
                            parent_snapshot_id=previous.id if previous else None, status="APPROVED",
                            reconciliation_id=rec.id, policy_ref=rec.policy_ref, policy_digest=rec.policy_digest,
                            canonical_ref=rec.canonical_ref, inputs=rec.inputs, counts=counts,
                            content_hash=content_hash, approved_by=principal.user_id,
                            approved_by_name=principal.name, approved_at=_now())
    session.add(snap)
    session.flush()
    if facts:
        session.execute(insert(SnapshotFact), [{
            "tenant_id": run.tenant_id, "snapshot_id": snap.id, "evidence_id": f["evidence_id"],
            "entity": f["entity"], "record_key": f["record_key"], "field": f["field"], "value": f["value"],
            "provenance": f["provenance"], "stale": f["stale"], "conflicted": f["conflicted"]} for f in facts])
    if rels:
        session.execute(insert(SnapshotRelationship), [{
            "tenant_id": run.tenant_id, "snapshot_id": snap.id, "evidence_id": e["evidence_id"], "name": e["name"],
            "from_entity": e["from_entity"], "from_key": e["from_key"], "to_entity": e["to_entity"],
            "to_key": e["to_key"], "sources": e["sources"]} for e in rels])
    _audit_event(session, platform, principal, principal.tenant_id, "evidence.snapshot_approved",
                 "EvidenceSnapshotApproved", "evidence_snapshot", str(snap.id), {
                     "run_id": str(run.id), "code": snap.code, "content_hash": content_hash,
                     "parent": previous.code if previous else None, "reconciliation_id": str(rec.id),
                     "policy": rec.policy_ref, "canonical": rec.canonical_ref, "counts": counts,
                     "inputs": [{k: i[k] for k in ("source_name", "execution_id", "output_hash", "as_of")}
                                for i in rec.inputs]})
    session.commit()
    return snap


# ---------------------------------------------------------------------------
# Snapshot reads and the Telco graph
# ---------------------------------------------------------------------------

def list_snapshots(session: Session, run: Run) -> list[EvidenceSnapshot]:
    return list(session.scalars(select(EvidenceSnapshot).where(EvidenceSnapshot.run_id == run.id,
                                                               EvidenceSnapshot.tenant_id == run.tenant_id)
                                .order_by(EvidenceSnapshot.snapshot_no.desc())))


def get_snapshot(session: Session, run: Run, snapshot_id: uuid.UUID) -> EvidenceSnapshot:
    snap = session.scalar(select(EvidenceSnapshot).where(EvidenceSnapshot.id == snapshot_id,
                                                         EvidenceSnapshot.run_id == run.id,
                                                         EvidenceSnapshot.tenant_id == run.tenant_id))
    if snap is None:
        raise ReconciliationNotFound("Snapshot not found")
    return snap


@dataclass
class Node:
    entity: str
    key: str
    values: dict[str, Any]
    evidence_ids: dict[str, str]
    flags: dict[str, list[str]]


def _nodes(session: Session, snap: EvidenceSnapshot, keys: set[tuple[str, str]]) -> dict[tuple[str, str], Node]:
    out: dict[tuple[str, str], Node] = {}
    if not keys:
        return out
    entities = {e for e, _ in keys}
    rows = session.scalars(select(SnapshotFact).where(SnapshotFact.snapshot_id == snap.id,
                                                      SnapshotFact.entity.in_(entities),
                                                      SnapshotFact.record_key.in_({k for _, k in keys})))
    for f in rows:
        if (f.entity, f.record_key) not in keys:
            continue
        n = out.setdefault((f.entity, f.record_key), Node(f.entity, f.record_key, {}, {}, {"stale": [],
                                                                                         "conflicted": []}))
        n.values[f.field] = f.value
        n.evidence_ids[f.field] = f.evidence_id
        if f.stale:
            n.flags["stale"].append(f.field)
        if f.conflicted:
            n.flags["conflicted"].append(f.field)
    return out


def graph(session: Session, run: Run, snapshot_id: uuid.UUID, product_id: str, depth: int = 4) -> dict[str, Any]:
    """Product -> services -> resources -> workflows -> billing (outgoing links), plus offers that include it and
    records that refer to it (lifecycle, prices, CPQ rules, aggregates, billing dependencies)."""
    snap = get_snapshot(session, run, snapshot_id)
    start = ("Product", rc._vjson([product_id]))
    seen = {start}
    edges: list[SnapshotRelationship] = []
    frontier = [start]
    for _ in range(depth):
        if not frontier:
            break
        nxt = []
        for entity, key in frontier:
            for e in session.scalars(select(SnapshotRelationship).where(
                    SnapshotRelationship.snapshot_id == snap.id, SnapshotRelationship.from_entity == entity,
                    SnapshotRelationship.from_key == key)):
                edges.append(e)
                target = (e.to_entity, e.to_key)
                if target not in seen:
                    seen.add(target)
                    nxt.append(target)
        frontier = nxt
    for e in session.scalars(select(SnapshotRelationship).where(  # who points at the product (offers, hierarchy)
            SnapshotRelationship.snapshot_id == snap.id, SnapshotRelationship.to_entity == "Product",
            SnapshotRelationship.to_key == start[1])):
        edges.append(e)
        seen.add((e.from_entity, e.from_key))
    canonical = _content(session, run.tenant_id, snap.canonical_ref)
    ref_fields = [(e["name"], f["name"]) for e in canonical["entities"] for f in e["fields"]
                  if f.get("type") == "reference" and f.get("references") == "Product" and e["name"] != "Product"]
    attached_keys: set[tuple[str, str]] = set()
    for entity, field_name in ref_fields:
        for key in session.scalars(select(SnapshotFact.record_key).where(
                SnapshotFact.snapshot_id == snap.id, SnapshotFact.entity == entity, SnapshotFact.field == field_name,
                SnapshotFact.value == type_coerce(product_id, JSONB))):
            attached_keys.add((entity, key))
    nodes = _nodes(session, snap, seen | attached_keys)
    if start not in nodes:
        raise ReconciliationNotFound(f"Product {product_id} is not in {snap.code}")
    attached: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for k in sorted(attached_keys):
        if k in nodes:
            attached[k[0]].append(nodes[k].__dict__)
    return {"snapshot": snap.code, "product": nodes[start].__dict__,
            "nodes": [nodes[k].__dict__ for k in sorted(seen) if k in nodes and k != start],
            "edges": [{"name": e.name, "from_entity": e.from_entity, "from_key": e.from_key,
                       "to_entity": e.to_entity, "to_key": e.to_key, "evidence_id": e.evidence_id,
                       "sources": e.sources} for e in edges],
            "attached": dict(attached)}

