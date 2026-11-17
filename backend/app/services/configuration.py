"""Scoring & AI Route (screen 04) and Review & Start (screen 05): configure, check, acknowledge, start, pin.

The draft lives on ``run.configuration`` while the run is CONFIGURING. ``start`` re-checks everything on the server,
then freezes a Run Manifest: exact artifact refs and digests (never "latest"), the approved snapshot, resolved
thresholds with their source, the AI routes and the acknowledged limitations. Reopening a started run always
reads the manifest, so newer published artifacts never change it.
"""

import hashlib
import json
import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.registry import Platform
from app.db.models import Artifact, EvidenceSnapshot, Market, ProductFamily, Run, RunManifest
from app.domain import configuration as cf
from app.domain.artifacts import ArtifactKind, format_ref, parse_ref
from app.domain.common import FieldError
from app.domain.permissions import Permission
from app.domain.runs import RunState
from app.ports.audit import AuditRecord
from app.ports.auth import Principal

from app.seed.telco import CANONICAL_KEY, PRECEDENCE_KEY, READINESS_KEY
from app.services import ai_runtime
from app.services import artifacts as registry
from app.services import readiness as readiness_service
from app.services.errors import ServiceError
from app.services.outbox import emit
from app.services.runs import RunNotEditable, RunVersionConflict, get_run

ROLES: dict[str, ArtifactKind] = {
    "detectorPack": ArtifactKind.DETECTOR_PACK,
    "scoringProfile": ArtifactKind.SCORING_PROFILE,
    "priorityPolicy": ArtifactKind.PRIORITY_POLICY,
    "policyPack": ArtifactKind.POLICY_PACK,
}
ROLE_LABELS = {"detectorPack": "Detector pack", "scoringProfile": "Scoring profile",
               "priorityPolicy": "Priority policy", "policyPack": "Policy pack"}


class ConfigurationError(ServiceError):
    code = "configuration_error"


class ConfigurationValidation(ConfigurationError):
    status_code = 422
    code = "validation_failed"


class ManifestNotFound(ConfigurationError):
    status_code = 404
    code = "not_found"


class StartBlocked(ConfigurationError):
    status_code = 409
    code = "start_blocked"


def _now() -> datetime:
    return datetime.now(UTC)


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"),
                                                 default=str).encode()).hexdigest()


def _draft(run: Run) -> dict[str, Any]:
    c = run.configuration or {}
    return {"selections": dict(c.get("selections", {})), "overrides": dict(c.get("overrides", {})),
            "acknowledgements": list(c.get("acknowledgements", []))}


# ---------------------------------------------------------------------------
# Context and candidates
# ---------------------------------------------------------------------------

def run_context(session: Session, run: Run) -> cf.RunContext:
    markets = session.scalars(select(Market.code).where(Market.tenant_id == run.tenant_id, Market.id.in_(run.market_ids)))
    families = session.scalars(select(ProductFamily.code).where(ProductFamily.tenant_id == run.tenant_id,
                                                               ProductFamily.id.in_(run.product_family_ids)))
    snap = readiness_service.current_snapshot(session, run)
    canonical = parse_ref(snap.canonical_ref)[0] if snap else CANONICAL_KEY
    return cf.RunContext(markets=frozenset(markets), families=frozenset(families),
                         objectives=frozenset(run.objectives), canonical=canonical)


def candidates(session: Session, tenant_id: str, kind: ArtifactKind) -> list[cf.Candidate]:
    out = []
    for a in session.scalars(select(Artifact).where(Artifact.tenant_id == tenant_id, Artifact.kind == kind.value)
                             .order_by(Artifact.key)):
        v = registry.selectable_version(session, a)
        if v is not None and registry.verify_digest(a, v):
            ver = registry.version_of(v)
            out.append(cf.Candidate(a.key, format_ref(a.key, ver), (ver.major, ver.minor), v.content))
    return out


def _resolve(session: Session, tenant_id: str, ref: str, kind: ArtifactKind) -> tuple[str, str, dict]:
    key, ver = parse_ref(ref) if "@" in ref else (ref, None)
    a, v = registry.resolve(session, tenant_id, key, ver, kind=kind)
    return format_ref(a.key, registry.version_of(v)), v.digest, v.content


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------

def evaluate(session: Session, run: Run) -> dict[str, Any]:
    """Recommendations, effective selections, resolved settings, AI routes and compatibility for the draft."""
    ctx = run_context(session, run)
    draft = _draft(run)
    recs: dict[str, cf.Recommendation] = {}
    selections: dict[str, dict] = {}
    contents: dict[str, dict | None] = {}
    for role, kind in ROLES.items():
        cands = candidates(session, run.tenant_id, kind)
        rec = cf.recommend(cands, ctx, ROLE_LABELS[role])
        recs[role] = rec
        chosen = draft["selections"].get(role)
        ref = chosen or rec.chosen
        sel: dict[str, Any] = {"ref": ref, "source": "chosen" if chosen else ("recommended" if ref else None),
                               "tie": rec.tie}
        contents[role] = None
        if ref:
            try:
                ref, digest, content = _resolve(session, run.tenant_id, ref, kind)
            except ServiceError:
                sel.update(ref=None, eligible=False, reasons=[f"{chosen} is no longer published"])
            else:
                ranked = next((r for r in rec.ranking if r.ref == ref), None)
                ranked = ranked or cf._rank(cf.Candidate(ref.split("@")[0], ref, (0, 0), content), ctx)
                sel.update(ref=ref, digest=digest, eligible=ranked.eligible, reasons=ranked.reasons,
                           objective_coverage=ranked.objective_coverage, description=content.get("description"))
                contents[role] = content
        selections[role] = sel
    settings = cf.resolve_settings(contents["scoringProfile"], contents["policyPack"], draft["overrides"])
    snap = readiness_service.current_snapshot(session, run)
    readiness = None
    if snap is not None:
        readiness = readiness_service.evidence_readiness(session, run.tenant_id, run.id, "snapshot").result
    _, plans = ai_runtime.plans(session, run.tenant_id)
    ai_routes = [{"task": p.task, "label": p.label, "compliant": p.compliant, "chain": [d.ref for d in p.chain]}
                 for p in plans]
    issues = cf.compatibility(selections=selections, settings=settings, policy=contents["policyPack"],
                              snapshot={"code": snap.code} if snap else None, readiness=readiness,
                              ai_routes=ai_routes)
    acked = {a["id"] for a in draft["acknowledgements"]}
    return {"context": ctx, "recommendations": recs, "selections": selections, "contents": contents,
            "settings": settings, "snapshot": snap, "readiness": readiness, "ai_routes": ai_routes,
            "issues": issues, "status": cf.status_of(issues), "draft": draft,
            "unacknowledged": [w for w in cf.warnings_to_acknowledge(issues) if w not in acked]}


# ---------------------------------------------------------------------------
# Changes
# ---------------------------------------------------------------------------

def _locked(session: Session, principal: Principal, run_id: uuid.UUID, expected_version: int) -> Run:
    run = get_run(session, principal.tenant_id, run_id, for_update=True)
    if run.state != RunState.CONFIGURING:
        raise RunNotEditable(f"The configuration is locked because the run is {run.state.lower()}")
    if run.version != expected_version:
        raise RunVersionConflict("This run was changed by someone else. Reload and try again.")
    return run


def _audit(session: Session, platform: Platform, principal: Principal, action: str, run: Run,
           payload: dict[str, Any]) -> None:
    platform.audit.record(AuditRecord(
        tenant_id=principal.tenant_id, actor_id=principal.user_id, actor_roles=principal.roles,
        auth_context=principal.auth_context, action=action, entity_type="run", entity_id=str(run.id),
        payload=payload), session=session)


def save(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID, expected_version: int,
         selections: dict[str, str | None] | None, overrides: dict[str, Any] | None) -> Run:
    run = _locked(session, principal, run_id, expected_version)
    draft = _draft(run)
    errors: list[FieldError] = []
    if selections is not None:
        for role, ref in selections.items():
            if role not in ROLES:
                errors.append(FieldError(f"selections.{role}", "Unknown configuration role"))
            elif ref:
                try:
                    _resolve(session, run.tenant_id, ref, ROLES[role])
                except ServiceError:
                    errors.append(FieldError(f"selections.{role}", f"No published {ROLE_LABELS[role].lower()} {ref}"))
            draft["selections"][role] = ref or None
        draft["selections"] = {k: v for k, v in draft["selections"].items() if v}
    if overrides is not None:
        policy_ref = draft["selections"].get("policyPack")
        policy = None
        if policy_ref:
            policy = _resolve(session, run.tenant_id, policy_ref, ArtifactKind.POLICY_PACK)[2]
        else:
            rec = cf.recommend(candidates(session, run.tenant_id, ArtifactKind.POLICY_PACK),
                               run_context(session, run), "Policy pack")
            policy = _resolve(session, run.tenant_id, rec.chosen, ArtifactKind.POLICY_PACK)[2] if rec.chosen else None
        clean = {k: v for k, v in overrides.items() if v is not None}
        errors += cf.check_overrides(clean, policy, Permission.CONFIG_SCORING.value in principal.permissions)
        draft["overrides"] = clean
    if errors:
        raise ConfigurationValidation("The configuration is not valid", errors)
    run.configuration = draft
    run.version += 1
    run.updated_at = _now()
    session.flush()
    _audit(session, platform, principal, "run.configuration_saved", run,
           {"selections": draft["selections"], "overrides": draft["overrides"]})
    session.commit()
    return run


def acknowledge(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID,
                expected_version: int, warning_ids: list[str]) -> Run:
    run = _locked(session, principal, run_id, expected_version)
    ev = evaluate(session, run)
    current = set(cf.warnings_to_acknowledge(ev["issues"]))
    unknown = [w for w in warning_ids if w not in current]
    if unknown:
        raise ConfigurationValidation("These warnings are not current", [FieldError("warning_ids", ", ".join(unknown))])
    draft = _draft(run)
    known = {a["id"] for a in draft["acknowledgements"]}
    now = _now().isoformat()
    draft["acknowledgements"] += [{"id": w, "actor_id": principal.user_id, "actor_name": principal.name, "at": now}
                                  for w in warning_ids if w not in known]
    run.configuration = draft
    run.version += 1
    session.flush()
    _audit(session, platform, principal, "run.limitations_acknowledged", run, {"warning_ids": warning_ids})
    session.commit()
    return run


def start(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID,
          expected_version: int) -> RunManifest:
    run = _locked(session, principal, run_id, expected_version)
    ev = evaluate(session, run)  # server re-check
    blocking = [i for i in ev["issues"] if i.severity == "BLOCKING"]
    if blocking:
        raise StartBlocked("The run cannot start yet", [FieldError(i.id, i.message) for i in blocking])
    if ev["unacknowledged"]:
        raise StartBlocked("Acknowledge the evidence limitations first",
                           [FieldError(w, "Not acknowledged") for w in ev["unacknowledged"]])
    snap: EvidenceSnapshot = ev["snapshot"]
    artifacts = []
    for role in ROLES:
        sel = ev["selections"][role]
        artifacts.append({"role": role, "ref": sel["ref"], "digest": sel["digest"]})
    for role, key, kind in (("readinessPolicy", READINESS_KEY, ArtifactKind.READINESS_POLICY),
                            ("precedencePolicy", PRECEDENCE_KEY, ArtifactKind.PRECEDENCE_POLICY)):
        try:
            ref, digest, _ = _resolve(session, run.tenant_id, key, kind)
            artifacts.append({"role": role, "ref": ref, "digest": digest})
        except ServiceError:
            pass
    canonical_ref = snap.canonical_ref
    ckey, cver = parse_ref(canonical_ref)
    ca, cv = registry.resolve(session, run.tenant_id, ckey, cver, kind=ArtifactKind.CANONICAL_SCHEMA)
    artifacts.append({"role": "canonicalSchema", "ref": canonical_ref, "digest": cv.digest})
    acked = {a["id"]: a for a in ev["draft"]["acknowledgements"]}
    manifest = {
        "run": {"id": str(run.id), "code": run.run_code, "name": run.name},
        "scope": {"markets": sorted(ev["context"].markets), "productFamilies": sorted(ev["context"].families),
                  "objectives": sorted(run.objectives), "periodStart": run.period_start.isoformat(),
                  "periodEnd": run.period_end.isoformat(), "ownerId": run.owner_id, "ownerName": run.owner_name},
        "snapshot": {"id": str(snap.id), "code": snap.code, "contentHash": snap.content_hash},
        "artifacts": artifacts,
        "settings": ev["settings"],
        "readiness": {"resultHash": ev["readiness"]["result_hash"], "kpis": ev["readiness"]["kpis"]}
        if ev["readiness"] else None,
        "ai": ai_runtime.pin(session, run.tenant_id),
        "recommendationTrace": {role: rec.trace for role, rec in ev["recommendations"].items()},
        "acknowledgements": [acked[i.id] for i in ev["issues"] if i.id in acked],
        "warnings": [{"id": i.id, "area": i.area, "message": i.message} for i in ev["issues"]
                     if i.severity == "WARNING"],
        "startedBy": principal.user_id,
        "startedAt": _now().isoformat(),
    }
    row = RunManifest(tenant_id=run.tenant_id, run_id=run.id, manifest=manifest, manifest_hash=_hash(manifest),
                      created_by=principal.user_id)
    session.add(row)
    run.state = RunState.ANALYSING.value
    run.started_at, run.started_by = _now(), principal.user_id
    run.version += 1
    run.updated_at = _now()
    session.flush()
    payload = {"run_id": str(run.id), "manifest_hash": row.manifest_hash, "snapshot": snap.code}
    _audit(session, platform, principal, "run.started", run, payload)
    emit(session, tenant_id=run.tenant_id, event_type="RunStarted", aggregate_type="run",
         aggregate_id=str(run.id), payload=payload)
    emit(session, tenant_id=run.tenant_id, event_type="AnalysisRequested", aggregate_type="run",
         aggregate_id=str(run.id), payload=payload)
    session.commit()
    on_started(session, platform, run)
    return row


def on_started(session: Session, platform: Platform, run: Run) -> None:
    """Hook for the analysis workflow (step 10 replaces this with the background job)."""
    return None


def manifest_of(session: Session, run: Run) -> RunManifest | None:
    return session.scalar(select(RunManifest).where(RunManifest.run_id == run.id,
                                                    RunManifest.tenant_id == run.tenant_id))
