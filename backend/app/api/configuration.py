"""Scoring & AI Route (screen 04), Review & Start (screen 05) and the Run Manifest (step 9)."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbDep, PlatformDep, require_permission
from app.api.schemas import ErrorResponse
from app.domain import configuration as cf
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import ai_runtime
from app.services import configuration as service
from app.services.runs import get_run

router = APIRouter(tags=["run-configuration"])

Configurator = Annotated[Principal, Depends(require_permission(Permission.RUN_CREATE))]
Starter = Annotated[Principal, Depends(require_permission(Permission.RUN_START))]
ERRORS = {
    404: {"model": ErrorResponse, "description": "Run not found"},
    409: {"model": ErrorResponse, "description": "version_conflict, not_editable or start_blocked (issues listed)"},
    422: {"model": ErrorResponse, "description": "validation_failed with field errors"},
}
SETTING_LABELS = {
    "recommendedThreshold": "Recommendation threshold", "reviewThreshold": "Review threshold",
    "belowReviewTreatment": "Below review threshold", "minimumEvidenceCoverage": "Minimum evidence coverage",
    "missingEvidenceTreatment": "Missing evidence", "criticalConflictTreatment": "Critical conflicts",
    "staleEvidenceTreatment": "Stale evidence", "technicalEquivalenceGate": "Technical equivalence gate",
    "executionReadinessGate": "Execution readiness gate", "humanReviewRequired": "Human review required",
}


class OptionOut(BaseModel):
    ref: str
    eligible: bool
    reasons: list[str]
    objective_coverage: int
    specificity: int
    priority: int


class SelectionOut(BaseModel):
    role: str
    label: str
    ref: str | None
    source: str | None = Field(description="chosen | recommended | null")
    recommended: str | None
    tie: list[str]
    eligible: bool | None
    reasons: list[str]
    objective_coverage: int | None
    description: str | None
    options: list[OptionOut]
    trace: list[str]


class SettingOut(BaseModel):
    name: str
    label: str
    value: Any
    source: str = Field(description="Run override | Policy pack | Scoring profile | Telco default")
    overridable: bool
    allowed: list[Any] | None


class IssueOut(BaseModel):
    id: str
    severity: str = Field(description="INFO | WARNING | BLOCKING")
    area: str
    message: str
    acknowledged: bool


class ConfigurationOut(BaseModel):
    run_id: uuid.UUID
    run_code: str
    run_name: str
    state: str
    version: int
    editable: bool
    started: bool
    can_override: bool
    selections: list[SelectionOut]
    settings: list[SettingOut]
    snapshot: dict | None
    readiness: dict | None
    ai: dict
    status: str = Field(description="Compatibility: PASS | WARNING | BLOCKED")
    issues: list[IssueOut]
    unacknowledged: list[str]
    scope: dict
    manifest_hash: str | None


class ConfigurationIn(BaseModel):
    version: int
    selections: dict[str, str | None] | None = Field(default=None, description="role -> KEY@x.y (null = recommended)")
    overrides: dict[str, Any] | None = Field(default=None, description="Authorised run overrides (config.scoring)")


class AckIn(BaseModel):
    version: int
    warning_ids: list[str] = Field(min_length=1, max_length=100)


class StartIn(BaseModel):
    version: int


class ManifestOut(BaseModel):
    run_id: uuid.UUID
    manifest: dict
    manifest_hash: str
    created_by: str
    created_at: datetime


def _scope(db, run) -> dict:
    ctx = service.run_context(db, run)
    return {"markets": sorted(ctx.markets), "product_families": sorted(ctx.families),
            "objectives": list(run.objectives), "period_start": run.period_start.isoformat(),
            "period_end": run.period_end.isoformat(), "owner_name": run.owner_name}


def _allowed(name: str) -> list[Any] | None:
    spec = cf.SETTING_VALUES[name]
    if spec[0] in ("int", "bool"):
        return None
    return list(spec)


def _out(db, run, user: Principal) -> ConfigurationOut:
    m = service.manifest_of(db, run)
    can_override = Permission.CONFIG_SCORING.value in user.permissions
    if m is not None:  # started: everything comes from the pinned manifest
        man = m.manifest
        arts = {a["role"]: a for a in man["artifacts"]}
        return ConfigurationOut(
            run_id=run.id, run_code=run.run_code, run_name=run.name, state=run.state, version=run.version,
            editable=False, started=True, can_override=False,
            selections=[SelectionOut(role=r, label=service.ROLE_LABELS[r], ref=arts[r]["ref"], source="pinned",
                                     recommended=None, tie=[], eligible=True, reasons=[], objective_coverage=None,
                                     description=None, options=[], trace=man["recommendationTrace"].get(r, []))
                        for r in service.ROLES],
            settings=[SettingOut(name=k, label=SETTING_LABELS[k], value=v["value"], source=v["source"],
                                 overridable=False, allowed=None) for k, v in man["settings"].items()],
            snapshot=man["snapshot"], readiness=man.get("readiness"),
            ai={"profile_ref": (man["ai"].get("profile") or {}).get("ref"), "routes": man["ai"].get("routes", {})},
            status="PASS", issues=[IssueOut(id=w["id"], severity="WARNING", area=w["area"], message=w["message"],
                                            acknowledged=True) for w in man["warnings"]],
            unacknowledged=[], scope=_scope(db, run), manifest_hash=m.manifest_hash)
    ev = service.evaluate(db, run)
    policy = ev["contents"]["policyPack"] or {}
    overridable = set(policy.get("overridable", []))
    acked = {a["id"] for a in ev["draft"]["acknowledgements"]}
    sels = []
    for role in service.ROLES:
        rec = ev["recommendations"][role]
        s = ev["selections"][role]
        sels.append(SelectionOut(
            role=role, label=service.ROLE_LABELS[role], ref=s.get("ref"), source=s.get("source"),
            recommended=rec.chosen, tie=rec.tie, eligible=s.get("eligible"), reasons=s.get("reasons", []),
            objective_coverage=s.get("objective_coverage"), description=s.get("description"),
            options=[OptionOut(ref=r.ref, eligible=r.eligible, reasons=r.reasons,
                               objective_coverage=r.objective_coverage, specificity=r.specificity,
                               priority=r.priority) for r in rec.ranking],
            trace=rec.trace))
    runtime, _ = ai_runtime.plans(db, run.tenant_id)
    snap = ev["snapshot"]
    return ConfigurationOut(
        run_id=run.id, run_code=run.run_code, run_name=run.name, state=run.state, version=run.version,
        editable=Permission.RUN_CREATE.value in user.permissions, started=False, can_override=can_override,
        selections=sels,
        settings=[SettingOut(name=k, label=SETTING_LABELS[k], value=v["value"], source=v["source"],
                             overridable=k in overridable, allowed=_allowed(k)) for k, v in ev["settings"].items()],
        snapshot={"id": str(snap.id), "code": snap.code, "contentHash": snap.content_hash} if snap else None,
        readiness={"kpis": ev["readiness"]["kpis"]} if ev["readiness"] else None,
        ai={"profile_ref": runtime.profile_ref, "strategy": (runtime.profile or {}).get("strategy"),
            "routes": ev["ai_routes"]},
        status=ev["status"],
        issues=[IssueOut(id=i.id, severity=i.severity, area=i.area, message=i.message, acknowledged=i.id in acked)
                for i in ev["issues"]],
        unacknowledged=ev["unacknowledged"], scope=_scope(db, run), manifest_hash=None)


@router.get("/runs/{run_id}/configuration", response_model=ConfigurationOut, responses=ERRORS,
            summary="Scoring & AI Route: recommended packs, settings with their source, compatibility")
def get_configuration(run_id: uuid.UUID, user: CurrentUser, db: DbDep) -> ConfigurationOut:
    return _out(db, get_run(db, user.tenant_id, run_id), user)


@router.put("/runs/{run_id}/configuration", response_model=ConfigurationOut, responses=ERRORS,
            summary="Choose packs and (authorised) run overrides")
def put_configuration(run_id: uuid.UUID, body: ConfigurationIn, user: Configurator, db: DbDep,
                      platform: PlatformDep) -> ConfigurationOut:
    run = service.save(db, platform, user, run_id, body.version, body.selections, body.overrides)
    return _out(db, run, user)


@router.post("/runs/{run_id}/configuration/acknowledgements", response_model=ConfigurationOut, responses=ERRORS,
             summary="Acknowledge evidence limitations (warnings) before Start")
def acknowledge(run_id: uuid.UUID, body: AckIn, user: Starter, db: DbDep, platform: PlatformDep) -> ConfigurationOut:
    run = service.acknowledge(db, platform, user, run_id, body.version, body.warning_ids)
    return _out(db, run, user)


@router.post("/runs/{run_id}/start", response_model=ManifestOut, status_code=status.HTTP_201_CREATED,
             responses=ERRORS, summary="Start analysis: server re-check, then freeze the Run Manifest")
def start(run_id: uuid.UUID, body: StartIn, user: Starter, db: DbDep, platform: PlatformDep) -> ManifestOut:
    m = service.start(db, platform, user, run_id, body.version)
    return ManifestOut(run_id=m.run_id, manifest=m.manifest, manifest_hash=m.manifest_hash, created_by=m.created_by,
                       created_at=m.created_at)


@router.get("/runs/{run_id}/manifest", response_model=ManifestOut, responses=ERRORS,
            summary="The frozen Run Manifest (exact ids, versions, digests)")
def get_manifest(run_id: uuid.UUID, user: CurrentUser, db: DbDep) -> ManifestOut:
    run = get_run(db, user.tenant_id, run_id)
    m = service.manifest_of(db, run)
    if m is None:
        raise service.ManifestNotFound("The run has not started yet")
    return ManifestOut(run_id=m.run_id, manifest=m.manifest, manifest_hash=m.manifest_hash, created_by=m.created_by,
                       created_at=m.created_at)
