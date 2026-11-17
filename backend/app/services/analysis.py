"""Analysis workflow (BUILD_PLAN step 10): Discover -> Normalize -> Analyze -> Assess -> Recommend, then the people
stages Review and Approve.

Everything reads the run's pinned manifest and its approved snapshot only, so the same snapshot + pinned packs
always reproduce the same findings and candidates (``result_hash``). Detector "today" is the snapshot approval
date, not the wall clock. Assess and Recommend are filled in by ``app.services.assessment`` (step 11).
"""

import hashlib
import json
import re
import uuid
from collections import defaultdict
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.adapters.registry import Platform
from app.db.models import (
    AnalysisRun,
    Candidate,
    DetectorFinding,
    EvidenceSnapshot,
    Market,
    ProductFamily,
    Run,
    RunManifest,
    SnapshotFact,
    SnapshotRelationship,
)
from app.domain import candidates as cand
from app.domain import detectors as det
from app.domain.artifacts import ArtifactKind, parse_ref
from app.domain.runs import RunState
from app.ports.audit import AuditRecord
from app.ports.auth import Principal
from app.services import artifacts as registry
from app.services.errors import ServiceError
from app.services.outbox import emit

ANALYSIS_JOB = "app.jobs.analysis.analyse_run"
SYSTEM_STAGES = ("Discover", "Normalize", "Analyze", "Assess", "Recommend")
PEOPLE_STAGES = ("Review", "Approve")


class AnalysisError(ServiceError):
    code = "analysis_error"


class AnalysisNotFound(AnalysisError):
    status_code = 404
    code = "not_found"


class AnalysisConflict(AnalysisError):
    status_code = 409
    code = "conflict"


def _now() -> datetime:
    return datetime.now(UTC)


def _norm(text: str | None) -> str:
    return re.sub(r"[^a-z0-9]+", "", (text or "").lower())


# ---------------------------------------------------------------------------
# Requesting
# ---------------------------------------------------------------------------

def manifest_for(session: Session, run: Run) -> RunManifest:
    m = session.scalar(select(RunManifest).where(RunManifest.run_id == run.id, RunManifest.tenant_id == run.tenant_id))
    if m is None:
        raise AnalysisConflict("The run has not started yet")
    return m


def request_analysis(session: Session, platform: Platform, run: Run, requested_by: str, *, reason: str = "START",
                     snapshot_id: uuid.UUID | None = None) -> AnalysisRun:
    m = manifest_for(session, run)
    seq = (session.scalar(select(func.max(AnalysisRun.seq)).where(AnalysisRun.run_id == run.id)) or 0) + 1
    stages = [{"stage": s, "status": "NOT_STARTED", "started_at": None, "finished_at": None, "detail": None}
              for s in SYSTEM_STAGES + PEOPLE_STAGES]
    a = AnalysisRun(tenant_id=run.tenant_id, run_id=run.id, seq=seq, reason=reason, status="QUEUED",
                    snapshot_id=snapshot_id or uuid.UUID(m.manifest["snapshot"]["id"]), manifest_hash=m.manifest_hash,
                    stages=stages, requested_by=requested_by)
    session.add(a)
    session.flush()
    a.job_id = platform.jobs.enqueue(ANALYSIS_JOB, str(a.id), job_id=f"analysis-{a.id}")
    emit(session, tenant_id=run.tenant_id, event_type="AnalysisQueued", aggregate_type="run",
         aggregate_id=str(run.id), payload={"analysis_id": str(a.id), "seq": seq, "reason": reason})
    session.commit()
    return a


def retry(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID) -> AnalysisRun:
    run = session.scalar(select(Run).where(Run.id == run_id, Run.tenant_id == principal.tenant_id).with_for_update())
    if run is None:
        raise AnalysisNotFound("Run not found")
    last = latest(session, run)
    if last is None or last.status != "FAILED":
        raise AnalysisConflict("Only a failed analysis can be retried")
    run.state = RunState.ANALYSING.value
    return request_analysis(session, platform, run, principal.user_id, reason=last.reason,
                            snapshot_id=last.snapshot_id)


def latest(session: Session, run: Run, *, succeeded: bool = False) -> AnalysisRun | None:
    stmt = select(AnalysisRun).where(AnalysisRun.run_id == run.id, AnalysisRun.tenant_id == run.tenant_id)
    if succeeded:
        stmt = stmt.where(AnalysisRun.status == "SUCCEEDED")
    return session.scalar(stmt.order_by(AnalysisRun.seq.desc()).limit(1))


# ---------------------------------------------------------------------------
# Discover: source products in scope
# ---------------------------------------------------------------------------

class Graph:
    """Snapshot facts as records + relationships, with evidence ids per record."""

    def __init__(self, session: Session, snap: EvidenceSnapshot) -> None:
        self.records: dict[tuple[str, str], dict[str, Any]] = defaultdict(dict)
        self.evidence: dict[tuple[str, str], list[str]] = defaultdict(list)
        self.conflicted: set[tuple[str, str, str]] = set()
        for f in session.scalars(select(SnapshotFact).where(SnapshotFact.snapshot_id == snap.id,
                                                            SnapshotFact.tenant_id == snap.tenant_id)):
            self.records[(f.entity, f.record_key)][f.field] = f.value
            self.evidence[(f.entity, f.record_key)].append(f.evidence_id)
            if f.conflicted:
                self.conflicted.add((f.entity, f.record_key, f.field))
        self.out: dict[tuple[str, str, str], list[tuple[str, str, str]]] = defaultdict(list)
        self.into: dict[tuple[str, str, str], list[tuple[str, str, str]]] = defaultdict(list)
        for r in session.scalars(select(SnapshotRelationship).where(SnapshotRelationship.snapshot_id == snap.id,
                                                                    SnapshotRelationship.tenant_id == snap.tenant_id)):
            self.out[(r.name, r.from_entity, r.from_key)].append((r.to_entity, r.to_key, r.evidence_id))
            self.into[(r.name, r.to_entity, r.to_key)].append((r.from_entity, r.from_key, r.evidence_id))
        self.by_entity: dict[str, list[str]] = defaultdict(list)
        for (e, k) in self.records:
            self.by_entity[e].append(k)

    def referencing(self, entity: str, field: str, value: Any) -> list[str]:
        return sorted(k for k in self.by_entity.get(entity, []) if self.records[(entity, k)].get(field) == value)


def _family_scope(session: Session, run: Run) -> dict[str, str]:
    """normalised family code/name -> selected family code (descendants included)."""
    fams = list(session.scalars(select(ProductFamily).where(ProductFamily.tenant_id == run.tenant_id)))
    children: dict[uuid.UUID | None, list[ProductFamily]] = defaultdict(list)
    for f in fams:
        children[f.parent_id].append(f)
    out: dict[str, str] = {}
    for f in fams:
        if f.id not in run.product_family_ids:
            continue
        stack = [f]
        while stack:
            cur = stack.pop()
            out.setdefault(_norm(cur.code), f.code)
            out.setdefault(_norm(cur.name), f.code)
            stack.extend(children.get(cur.id, []))
    return out


def discover(session: Session, run: Run, g: Graph) -> dict[str, Any]:
    scope = _family_scope(session, run)
    markets = list(session.scalars(select(Market).where(Market.tenant_id == run.tenant_id,
                                                         Market.id.in_(run.market_ids))))
    market_names = {_norm(m.code) for m in markets} | {_norm(m.name) for m in markets}
    included: dict[str, str] = {}
    excluded: dict[str, list[str]] = {"invalid": [], "outOfScopeFamily": [], "outOfScopeMarket": []}
    for key in sorted(g.by_entity.get("Product", [])):
        rec = g.records[("Product", key)]
        pid = json.loads(key)[0]
        if not rec.get("productName") or not rec.get("productFamily") or \
                ("Product", key, "productName") in g.conflicted:
            excluded["invalid"].append(pid)
            continue
        fam = scope.get(_norm(rec["productFamily"]))
        if fam is None:
            excluded["outOfScopeFamily"].append(pid)
            continue
        if rec.get("market") and _norm(rec["market"]) not in market_names:
            excluded["outOfScopeMarket"].append(pid)
            continue
        included[pid] = fam
    return {"snapshotProducts": len(g.by_entity.get("Product", [])), "sourceProducts": len(included),
            "excluded": {k: len(v) for k, v in excluded.items()}, "excludedProducts": excluded,
            "products": included}


# ---------------------------------------------------------------------------
# Normalize: product features
# ---------------------------------------------------------------------------

def _num(v: Any) -> float | None:
    try:
        return None if v is None else float(v)
    except (TypeError, ValueError):
        return None


def _int(v: Any) -> int | None:
    n = _num(v)
    return None if n is None else int(n)


def _date(v: Any) -> date | None:
    try:
        return date.fromisoformat(str(v)) if v else None
    except ValueError:
        return None


def _latest(g: Graph, entity: str, pid: str, order_field: str) -> tuple[str, dict] | None:
    keys = g.referencing(entity, "productId", pid)
    if not keys:
        return None
    k = max(keys, key=lambda k: str(g.records[(entity, k)].get(order_field) or ""))
    return k, g.records[(entity, k)]


def features(g: Graph, products: dict[str, str]) -> list[det.ProductFeatures]:
    out = []
    for pid, fam in sorted(products.items()):
        pk = json.dumps([pid])
        rec = g.records[("Product", pk)]
        p = det.ProductFeatures(product_id=pid, name=rec.get("productName") or "", family=fam,
                                status=rec.get("status"), segment=rec.get("segment"), market=rec.get("market"),
                                channel=rec.get("channel"), parent=rec.get("parentProductId"))
        ev: dict[str, set[str]] = defaultdict(set)
        ev["product"].update(g.evidence[("Product", pk)])
        for _, okey, eid in g.into.get(("INCLUDES", "Product", pk), []):
            p.offers.add(json.loads(okey)[0])
            ev["product"].add(eid)
        rules = g.referencing("CpqRule", "productId", pid)
        if rules:
            p.cpq_rule_count = len(rules)
            for k in rules:
                r = g.records[("CpqRule", k)]
                if r.get("ruleType") in ("CONFIGURATION", "ELIGIBILITY"):
                    p.cpq_options.add(str(r.get("attributeOptionId")))
                ev["cpq"].update(g.evidence[("CpqRule", k)])
        for k in g.referencing("PriceBookEntry", "productId", pid):
            r = g.records[("PriceBookEntry", k)]
            price = _num(r.get("listPrice"))
            if price is not None and r.get("currency"):
                p.prices[r["currency"]] = price
            ev["price"].update(g.evidence[("PriceBookEntry", k)])
        lc = _latest(g, "ProductLifecycle", pid, "effectiveFrom")
        if lc:
            k, r = lc
            p.lifecycle, p.end_of_sale, p.sunset = r.get("lifecycleStatus"), _date(r.get("endOfSaleDate")), \
                _date(r.get("sunsetDate"))
            p.replacement = r.get("replacementProductId")
            ev["lifecycle"].update(g.evidence[("ProductLifecycle", k)])
        for _, skey, eid in g.out.get(("DECOMPOSES_TO", "Product", pk), []):
            sid = json.loads(skey)[0]
            p.services.add(sid)
            ev["technical"].add(eid)
            srec = g.records.get(("ServiceSpec", skey), {})
            if srec.get("serviceType"):
                p.service_types.add(str(srec["serviceType"]).lower())
            ev["technical"].update(g.evidence.get(("ServiceSpec", skey), []))
            for _, rkey, reid in g.out.get(("USES_RESOURCE", "ServiceSpec", skey), []):
                p.resources.add(json.loads(rkey)[0])
                ev["technical"].add(reid)
        wf_keys = {(wk, e) for (_, wk, e) in g.out.get(("PROVISIONED_BY", "Product", pk), [])}
        for sid in p.services:
            wf_keys |= {(wk, e) for (_, wk, e) in g.out.get(("PROVISIONED_BY", "ServiceSpec", json.dumps([sid])), [])}
        for wk, eid in sorted(wf_keys):
            w = g.records.get(("ProvisioningWorkflow", wk), {})
            p.workflows.append({"id": json.loads(wk)[0], "legacy": bool(w.get("legacyFlag")),
                                "compatibility": w.get("targetCompatibility")})
            ev["provisioning"].update(g.evidence.get(("ProvisioningWorkflow", wk), []) + [eid])
        inv = [k for sid in sorted(p.services) for k in g.referencing("InventoryAggregate", "serviceSpecId", sid)]
        if inv:
            recs = [g.records[("InventoryAggregate", k)] for k in inv]
            p.inventory_active = sum(_int(r.get("activeCount")) or 0 for r in recs if r.get("status") == "ACTIVE")
            variants = [_int(r.get("variantCount")) for r in recs if r.get("variantCount") is not None]
            p.inventory_variants = max(variants) if variants else None
            p.inventory_legacy = any(bool(r.get("legacyDependency")) for r in recs)
            for k in inv:
                ev["inventory"].update(g.evidence[("InventoryAggregate", k)])
        oba = _latest(g, "OrderAssuranceAggregate", pid, "periodEnd")
        if oba:
            k, r = oba
            p.orders, p.fallout, p.manual_touch = _int(r.get("orderCount")), _int(r.get("falloutOrders")), \
                _int(r.get("manualTouchOrders"))
            p.incidents, p.provisioning_exceptions = _int(r.get("incidentCount")), \
                _int(r.get("provisioningExceptions"))
            p.handling_minutes = _num(r.get("avgHandlingMinutes"))
            ev["order_billing"].update(g.evidence[("OrderAssuranceAggregate", k)])
        cra = _latest(g, "CustomerRevenueAggregate", pid, "periodEnd")
        if cra:
            k, r = cra
            p.subscriptions, p.protected_contracts = _int(r.get("activeSubscriptions")), \
                _int(r.get("protectedPriceContracts"))
            p.mrr, p.currency = _num(r.get("mrr")), r.get("currency")
            ev["customer"].update(g.evidence[("CustomerRevenueAggregate", k)])
        deps = g.referencing("BillingDependency", "productId", pid)
        if deps:
            p.legacy_billing = False
            for k in deps:
                r = g.records[("BillingDependency", k)]
                if r.get("dependencyStatus") == "DEPRECATED":
                    continue
                p.billing_systems.add(str(r.get("billingSystemId")))
                p.legacy_billing = p.legacy_billing or bool(r.get("legacyBillingCode"))
                ev["billing"].update(g.evidence[("BillingDependency", k)])
        p.evidence = {k: sorted(v) for k, v in ev.items()}
        out.append(p)
    return out


# ---------------------------------------------------------------------------
# The workflow
# ---------------------------------------------------------------------------

def _stage(a: AnalysisRun, name: str, status: str, detail: Any = None) -> None:
    stages = [dict(s) for s in a.stages]
    for s in stages:
        if s["stage"] == name:
            if status == "RUNNING":
                s["started_at"] = _now().isoformat()
            if status in ("COMPLETE", "FAILED"):
                s["finished_at"] = _now().isoformat()
            s["status"] = status
            if detail is not None:
                s["detail"] = detail
    a.stages = stages


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                 default=str).encode()).hexdigest()


def pinned_content(session: Session, tenant_id: str, manifest: dict, role: str, kind: ArtifactKind) -> tuple[str, dict]:
    ref = next(a["ref"] for a in manifest["artifacts"] if a["role"] == role)
    key, ver = parse_ref(ref)
    _, v = registry.resolve(session, tenant_id, key, ver, kind=kind)
    return ref, v.content


def _assign_codes(session: Session, run: Run, drafts: list[cand.CandidateDraft]) -> dict[str, str]:
    """Same product set as an earlier candidate of the run -> same code; otherwise the next CAND-NNNN."""
    existing = {tuple(c.subjects): c.code for c in session.scalars(
        select(Candidate).where(Candidate.run_id == run.id, Candidate.tenant_id == run.tenant_id))}
    used = [int(c.split("-")[1]) for c in existing.values()]
    nxt = max(used, default=0) + 1
    codes = {}
    for d in drafts:
        code = existing.get(tuple(d.subjects))
        if code is None:
            code = f"CAND-{nxt:04d}"
            nxt += 1
        codes[d.group_key] = code
    return codes


def run_analysis(session: Session, platform: Platform, analysis_id: uuid.UUID) -> AnalysisRun:
    a = session.get(AnalysisRun, analysis_id)
    if a is None or a.status not in ("QUEUED", "RUNNING"):
        return a
    run = session.get(Run, a.run_id)
    a.status = "RUNNING"
    session.commit()
    current = "Discover"
    try:
        manifest = manifest_for(session, run).manifest
        snap = session.get(EvidenceSnapshot, a.snapshot_id)
        _stage(a, "Discover", "RUNNING")
        g = Graph(session, snap)
        sp = discover(session, run, g)
        a.source_products = sp
        _stage(a, "Discover", "COMPLETE", {"sourceProducts": sp["sourceProducts"], "excluded": sp["excluded"]})

        current = "Normalize"
        _stage(a, "Normalize", "RUNNING")
        feats = features(g, sp["products"])
        _stage(a, "Normalize", "COMPLETE", {"products": len(feats)})

        current = "Analyze"
        _stage(a, "Analyze", "RUNNING")
        pack_ref, pack = pinned_content(session, run.tenant_id, manifest, "detectorPack", ArtifactKind.DETECTOR_PACK)
        as_of = snap.approved_at.date()
        findings, summary = det.run_detectors(feats, pack, as_of)
        normalised = cand.normalise(findings, run_id=str(run.id), snapshot_id=str(snap.id), pack=pack,
                                    pack_ref=pack_ref, snapshot_products={json.loads(k)[0] for k in
                                                                          g.by_entity.get("Product", [])},
                                    in_scope=set(sp["products"]))
        codes = _assign_codes(session, run, normalised.candidates)
        previous = list(session.scalars(select(Candidate).where(Candidate.run_id == run.id,
                                                                Candidate.status == "ACTIVE",
                                                                Candidate.analysis_id != a.id)))
        rows: dict[str, Candidate] = {}
        for d in normalised.candidates:
            c = Candidate(tenant_id=run.tenant_id, run_id=run.id, analysis_id=a.id, code=codes[d.group_key],
                          group_key=d.group_key, subjects=d.subjects, primary_type=d.primary_type,
                          supporting_types=d.supporting_types, max_signal=d.max_signal,
                          finding_count=len(d.finding_keys), evidence_ids=d.evidence_ids, status="ACTIVE",
                          lineage={"snapshot": snap.code, "analysisSeq": a.seq, "detectorPack": pack_ref})
            session.add(c)
            rows[d.group_key] = c
        session.flush()
        by_finding = {fk: rows[d.group_key].id for d in normalised.candidates for fk in d.finding_keys}
        for f in findings:
            session.add(DetectorFinding(
                tenant_id=run.tenant_id, analysis_id=a.id, run_id=run.id, finding_key=f.key,
                detector_type=f.detector_type, detector_version=f.detector_version, subjects=f.subjects,
                signal=f.signal, threshold=f.threshold, band=f.band, factors=f.factors, coverage=f.coverage,
                evidence_ids=f.evidence_ids, notes=f.notes, status=normalised.finding_status[f.key],
                candidate_id=by_finding.get(f.key)))
        successors = {c.code: c for c in rows.values()}
        for old in previous:  # an earlier analysis's candidates are replaced (lineage kept, never deleted)
            new = successors.get(old.code)
            old.status = "SUPERSEDED"
            old.superseded_by = new.id if new else None
            old.lineage = {**old.lineage, "supersededIn": a.seq,
                           "reason": f"Reassessed on {snap.code}" if new else f"No longer detected on {snap.code}"}
        a.counts = {**normalised.counts, "superseded": len(previous)}
        a.detector_summary = summary
        _stage(a, "Analyze", "COMPLETE", {"rawFindings": len(findings), "candidates": len(rows)})
        session.flush()

        from app.services import assessment  # step 11: scoring, priority, blockers, recommendation

        current = "Assess"
        _stage(a, "Assess", "RUNNING")
        detail = assessment.assess(session, run, a, manifest, feats, snap)
        _stage(a, "Assess", "COMPLETE", detail)
        current = "Recommend"
        _stage(a, "Recommend", "RUNNING")
        detail = assessment.recommend(session, run, a, manifest, feats, snap)
        _stage(a, "Recommend", "COMPLETE", detail)
        _stage(a, "Review", "WAITING")

        a.result_hash = _hash({"findings": [f.as_dict() for f in findings],
                               "candidates": [[c.code, c.group_key, c.primary_type, c.supporting_types,
                                               c.max_signal, c.evidence_ids] for c in rows.values()],
                               "assessment": assessment.result_digest(session, a)})
        a.status = "SUCCEEDED"
        a.finished_at = _now()
        run.state = RunState.REVIEW.value
        run.updated_at = _now()
        payload = {"analysis_id": str(a.id), "counts": a.counts, "result_hash": a.result_hash}
        platform.audit.record(AuditRecord(tenant_id=run.tenant_id, actor_id="system:analysis", actor_roles=(),
                                          auth_context={}, action="analysis.completed", entity_type="run",
                                          entity_id=str(run.id), payload=payload), session=session)
        emit(session, tenant_id=run.tenant_id, event_type="AnalysisCompleted", aggregate_type="run",
             aggregate_id=str(run.id), payload=payload)
        session.commit()
    except Exception as exc:  # noqa: BLE001 - record any failure on the analysis
        session.rollback()
        a = session.get(AnalysisRun, analysis_id)
        run = session.get(Run, a.run_id)
        _stage(a, current, "FAILED", {"error": str(exc)[:500]})
        a.status, a.error, a.finished_at = "FAILED", str(exc)[:2000], _now()
        run.state = RunState.FAILED.value
        emit(session, tenant_id=run.tenant_id, event_type="AnalysisFailed", aggregate_type="run",
             aggregate_id=str(run.id), payload={"analysis_id": str(a.id), "stage": current})
        session.commit()
    return a
