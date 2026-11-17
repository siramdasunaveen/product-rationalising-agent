"""Evidence readiness of a run (step 7, screen 11).

Builds the evidence input for ``app.domain.readiness.compute`` either from the run's current APPROVED snapshot
(the default: what decisions are based on, CLAUDE.md rule 6) or, as a preview, from the latest successful
reconciliation that has not been approved yet. The published readiness policy (TELCO-READINESS) decides
importance, measures, texts and the domains each disposition needs.
"""

from dataclasses import dataclass
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import (
    EvidenceSnapshot,
    ReconciledRecord,
    ReconciledRelationship,
    ReconciliationRun,
    Run,
    SnapshotFact,
    SnapshotRelationship,
)
from app.domain import readiness as rd
from app.domain.artifacts import ArtifactKind, format_ref
from app.seed.telco import READINESS_KEY
from app.services import artifacts as registry
from app.services.errors import ServiceError
from app.services.evidence import selected_domains
from app.services.reconciliation import _run, latest

Source = Literal["snapshot", "preview"]


class ReadinessUnavailable(ServiceError):
    status_code = 409
    code = "conflict"


@dataclass
class Readiness:
    run_id: str
    source: Source
    based_on: dict[str, Any]
    policy_ref: str
    result: dict[str, Any]


def stale_domains(inputs: list[dict], summary: dict | None) -> set[str]:
    """A domain is stale when every source that fed it is older than its freshness limit."""
    stale_ids = {s["source_id"] for s in (summary or {}).get("stale_sources", [])}
    by_domain: dict[str, list[str]] = {}
    for i in inputs:
        by_domain.setdefault(i["domain"], []).append(i["source_id"])
    return {d for d, ids in by_domain.items() if ids and all(s in stale_ids for s in ids)}


def _from_snapshot(session: Session, snap: EvidenceSnapshot) -> rd.Evidence:
    ev = rd.Evidence()
    for f in session.scalars(select(SnapshotFact).where(SnapshotFact.snapshot_id == snap.id,
                                                        SnapshotFact.tenant_id == snap.tenant_id)):
        ev.records.setdefault((f.entity, f.record_key), {})[f.field] = f.value
        if f.conflicted:
            ev.conflicted.add((f.entity, f.record_key, f.field))
    for r in session.scalars(select(SnapshotRelationship).where(SnapshotRelationship.snapshot_id == snap.id,
                                                                SnapshotRelationship.tenant_id == snap.tenant_id)):
        ev.edges.append((r.name, r.from_entity, r.from_key, r.to_entity, r.to_key))
    rec = session.get(ReconciliationRun, snap.reconciliation_id)
    ev.stale_domains = stale_domains(snap.inputs, rec.summary if rec else None)
    return ev


def _from_reconciliation(session: Session, rec: ReconciliationRun) -> rd.Evidence:
    ev = rd.Evidence()
    for r in session.scalars(select(ReconciledRecord).where(ReconciledRecord.reconciliation_id == rec.id,
                                                            ReconciledRecord.tenant_id == rec.tenant_id)):
        values = ev.records.setdefault((r.entity, r.record_key), {})
        values.update(r.values)
        for field, prov in r.provenance.items():
            if prov.get("conflicted"):
                ev.conflicted.add((r.entity, r.record_key, field))
                values.setdefault(field, None)
    for r in session.scalars(select(ReconciledRelationship).where(
            ReconciledRelationship.reconciliation_id == rec.id, ReconciledRelationship.tenant_id == rec.tenant_id)):
        ev.edges.append((r.name, r.from_entity, r.from_key, r.to_entity, r.to_key))
    ev.stale_domains = stale_domains(rec.inputs, rec.summary)
    return ev


def current_snapshot(session: Session, run: Run) -> EvidenceSnapshot | None:
    return session.scalar(select(EvidenceSnapshot).where(EvidenceSnapshot.run_id == run.id,
                                                         EvidenceSnapshot.tenant_id == run.tenant_id,
                                                         EvidenceSnapshot.status == "APPROVED"))


def evidence_readiness(session: Session, tenant_id: str, run_id, source: Source = "snapshot") -> Readiness:
    run = _run(session, tenant_id, run_id)
    try:
        artifact, version = registry.resolve(session, tenant_id, READINESS_KEY, kind=ArtifactKind.READINESS_POLICY)
    except ServiceError as exc:
        raise ReadinessUnavailable(f"Publish the readiness policy first ({exc})") from exc
    policy_ref = format_ref(artifact.key, registry.version_of(version))
    selected = [d.value for d in selected_domains(run)]

    based_on: dict[str, Any]
    if source == "preview":
        rec = latest(session, run, succeeded=True)
        if rec is None:
            evidence, based_on = rd.Evidence(), {"kind": "none", "label": "No reconciliation yet"}
        else:
            evidence = _from_reconciliation(session, rec)
            based_on = {"kind": "preview", "reconciliation_id": str(rec.id), "label": "Preview of the latest "
                        "reconciliation (not approved)", "at": (rec.finished_at or rec.created_at).isoformat()}
    else:
        snap = current_snapshot(session, run)
        if snap is None:
            evidence, based_on = rd.Evidence(), {"kind": "none", "label": "No approved evidence snapshot yet"}
        else:
            evidence = _from_snapshot(session, snap)
            based_on = {"kind": "snapshot", "snapshot_id": str(snap.id), "code": snap.code,
                        "label": f"Based on {snap.code}", "content_hash": snap.content_hash,
                        "at": snap.approved_at.isoformat()}
    result = rd.compute(version.content, evidence, selected)
    return Readiness(run_id=str(run.id), source=source, based_on=based_on, policy_ref=policy_ref, result=result)

