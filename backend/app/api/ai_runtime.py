"""AI runtime (step 8, screen 16): strategy, approved deployments, task routes with governance checks, secrets."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from fastapi import APIRouter, Depends, Path, Query, status
from pydantic import BaseModel, Field

from app.api.deps import DbDep, PlatformDep, require_permission
from app.api.schemas import ErrorResponse
from app.domain import ai_runtime as rt
from app.domain.permissions import Permission
from app.ports.ai import AIRequest
from app.ports.auth import Principal
from app.services import ai_runtime as service

router = APIRouter(prefix="/ai-runtime", tags=["ai-runtime"])

Viewer = Annotated[Principal, Depends(require_permission(Permission.REGISTRY_READ))]
Admin = Annotated[Principal, Depends(require_permission(Permission.CONFIG_AI))]
ERRORS = {
    404: {"model": ErrorResponse, "description": "No runtime profile or secret"},
    409: {"model": ErrorResponse, "description": "conflict: the profile changed meanwhile"},
    422: {"model": ErrorResponse, "description": "validation_failed with field errors"},
}

STRATEGY_TEXT = {
    rt.Strategy.CUSTOMER_REGISTRY: ("Customer model registry", "Use an approved model from your own registry"),
    rt.Strategy.PLATFORM_MANAGED: ("Platform-managed", "Use the approved platform model configuration"),
    rt.Strategy.BYO_ENDPOINT: ("Bring your own endpoint", "Connect a customer-approved hosted model endpoint"),
    rt.Strategy.NO_LLM: ("No-LLM mode", "Rules, scoring and templates only"),
}


class CheckOut(BaseModel):
    key: str
    label: str
    status: str = Field(description="PASS | WARNING | FAIL")
    message: str


class DeploymentOut(BaseModel):
    key: str
    ref: str
    status: str
    display_name: str
    provider: str
    model: str | None
    hosting: str
    region: str
    quality_tier: int | None
    cost_tier: int | None
    secret_ref: str | None
    secret_set: bool | None


class RouteOut(BaseModel):
    task: str
    label: str
    mode: str = Field(description="LLM | DETERMINISTIC")
    primary: str | None = Field(description="Configured primary deployment key/ref")
    fallback: str | None
    chain: list[str] = Field(description="Approved deployments that will be tried, in order (max 2)")
    deterministic_fallback: bool = True
    checks: list[CheckOut]
    compliant: bool
    rejected: dict[str, list[str]]
    trace: list[str]


class StrategyOut(BaseModel):
    key: str
    label: str
    description: str
    selected: bool


class RuntimeOut(BaseModel):
    profile_ref: str | None
    strategy: str | None
    routing_policy: str | None
    failure_treatment: str | None
    ai_confidence_threshold: int | None
    constraints: dict | None
    strategies: list[StrategyOut]
    deployments: list[DeploymentOut]
    routes: list[RouteOut]
    can_edit: bool


class RouteIn(BaseModel):
    task: rt.Task
    mode: Literal["LLM", "DETERMINISTIC"] = "LLM"
    primary: str | None = Field(default=None, max_length=80)
    fallback: str | None = Field(default=None, max_length=80)


class RuntimeIn(BaseModel):
    expected_ref: str = Field(description="profile_ref you loaded (optimistic concurrency)")
    strategy: rt.Strategy | None = None
    routing_policy: rt.RoutingPolicy | None = None
    failure_treatment: rt.FailureTreatment | None = None
    ai_confidence_threshold: int | None = Field(default=None, ge=0, le=100)
    routes: list[RouteIn] | None = None


class SecretOut(BaseModel):
    name: str
    updated_by: str
    updated_at: datetime


class SecretIn(BaseModel):
    value: str = Field(min_length=1, max_length=4000, description="Write-only; never returned")


class InvocationOut(BaseModel):
    id: uuid.UUID
    run_id: uuid.UUID | None
    task: str
    profile_ref: str | None
    deployment_ref: str | None
    model: str | None
    source: str
    attempts: int
    chain: list[str]
    errors: list[str]
    needs_human_review: bool
    created_at: datetime


class TestOut(BaseModel):
    task: str
    source: str = Field(description="llm | fallback")
    deployment: str | None
    model: str | None
    attempts: int
    errors: list[str]


def _credential(plan: rt.RoutePlan, secrets: set[str]) -> list[CheckOut]:
    missing = [d.ref for d in plan.chain if d.content.get("secretRef") and d.content["secretRef"] not in secrets]
    if not missing:
        return []
    return [CheckOut(key="CREDENTIAL", label="Credential", status="WARNING",
                     message=f"No credential stored for {', '.join(missing)}; the template is used until it is set "
                             "(or PRA_ANTHROPIC_API_KEY for Anthropic)")]


def _view(db, principal: Principal) -> RuntimeOut:
    r, plans = service.plans(db, principal.tenant_id)
    secrets = {s.name for s in service.list_secrets(db, principal.tenant_id)}
    deps = {d.key: d for d in r.deployments.values()}
    p = r.profile or {}
    routes_cfg = {x["task"]: x for x in p.get("routes", [])}
    return RuntimeOut(
        profile_ref=r.profile_ref, strategy=p.get("strategy"), routing_policy=p.get("routingPolicy"),
        failure_treatment=p.get("failureTreatment"), ai_confidence_threshold=p.get("aiConfidenceThreshold"),
        constraints=p.get("constraints"),
        strategies=[StrategyOut(key=s.value, label=STRATEGY_TEXT[s][0], description=STRATEGY_TEXT[s][1],
                                selected=p.get("strategy") == s.value) for s in rt.Strategy],
        deployments=[DeploymentOut(
            key=d.key, ref=d.ref, status=d.status, display_name=d.content["displayName"],
            provider=d.content["provider"], model=d.content.get("model"), hosting=d.content["hosting"],
            region=d.content["region"], quality_tier=d.content.get("qualityTier"),
            cost_tier=d.content.get("costTier"), secret_ref=d.content.get("secretRef"),
            secret_set=(d.content["secretRef"] in secrets) if d.content.get("secretRef") else None)
            for d in sorted(deps.values(), key=lambda x: x.key)],
        routes=[RouteOut(
            task=pl.task, label=pl.label, mode=routes_cfg.get(pl.task, {}).get("mode", "LLM"),
            primary=routes_cfg.get(pl.task, {}).get("primary"), fallback=routes_cfg.get(pl.task, {}).get("fallback"),
            chain=[d.ref for d in pl.chain], checks=[CheckOut(**c.__dict__) for c in pl.checks] + _credential(pl, secrets),
            compliant=pl.compliant, rejected=pl.rejected, trace=pl.trace) for pl in plans],
        can_edit=Permission.CONFIG_AI.value in principal.permissions)


@router.get("", response_model=RuntimeOut, summary="AI strategy, approved deployments and task routes")
def get_runtime(user: Viewer, db: DbDep) -> RuntimeOut:
    return _view(db, user)


@router.put("", response_model=RuntimeOut, responses=ERRORS,
            summary="Change strategy, routing or task routes (publishes a new profile version)")
def put_runtime(body: RuntimeIn, user: Admin, db: DbDep, platform: PlatformDep) -> RuntimeOut:
    changes = {"strategy": body.strategy, "routingPolicy": body.routing_policy,
               "failureTreatment": body.failure_treatment, "aiConfidenceThreshold": body.ai_confidence_threshold}
    if body.routes is not None:
        changes["routes"] = [{k: v for k, v in {"task": r.task.value, "mode": r.mode, "primary": r.primary,
                                                 "fallback": r.fallback}.items() if v} for r in body.routes]
    service.save_profile(db, platform, user, body.expected_ref,
                         {k: (v.value if hasattr(v, "value") else v) for k, v in changes.items()})
    return _view(db, user)


@router.post("/test/{task}", response_model=TestOut, summary="Send a small test request through a task's route")
def test_route(task: rt.Task, user: Admin, db: DbDep, platform: PlatformDep) -> TestOut:
    schema = {"type": "object", "required": ["ok"], "additionalProperties": False,
              "properties": {"ok": {"type": "boolean"}, "note": {"type": "string", "maxLength": 200}}}
    result = service.run_task(db, platform, user.tenant_id, user.user_id, task, AIRequest(
        task=task.value, system="You check connectivity for a governed agent. Answer with JSON only.",
        prompt='Return {"ok": true, "note": "<one short sentence>"}.', schema=schema, max_tokens=300),
        fallback=lambda: {"ok": False, "note": "Deterministic template used"})
    db.commit()
    return TestOut(task=task.value, source=result.source, deployment=result.deployment, model=result.model,
                   attempts=result.attempts, errors=result.errors)


@router.get("/secrets", response_model=list[SecretOut], summary="Names of stored AI secrets (values never returned)")
def secrets(user: Admin, db: DbDep) -> list[SecretOut]:
    return [SecretOut(name=s.name, updated_by=s.updated_by, updated_at=s.updated_at)
            for s in service.list_secrets(db, user.tenant_id)]


SecretName = Annotated[str, Path(pattern=service.SECRET_NAME_RE)]


@router.put("/secrets/{name}", response_model=SecretOut, responses=ERRORS, summary="Store an AI secret (encrypted)")
def put_secret(name: SecretName, body: SecretIn, user: Admin, db: DbDep, platform: PlatformDep) -> SecretOut:
    s = service.set_secret(db, platform, user, name, body.value)
    return SecretOut(name=s.name, updated_by=s.updated_by, updated_at=s.updated_at)


@router.delete("/secrets/{name}", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS,
               summary="Delete an AI secret")
def delete_secret(name: SecretName, user: Admin, db: DbDep, platform: PlatformDep) -> None:
    service.delete_secret(db, platform, user, name)


@router.get("/invocations", response_model=list[InvocationOut], summary="Recent AI task executions (audit)")
def list_invocations(user: Admin, db: DbDep, run_id: Annotated[uuid.UUID | None, Query()] = None,
                     limit: Annotated[int, Query(ge=1, le=200)] = 20) -> list[InvocationOut]:
    return [InvocationOut.model_validate(i, from_attributes=True)
            for i in service.invocations(db, user.tenant_id, run_id=run_id, limit=limit)]
