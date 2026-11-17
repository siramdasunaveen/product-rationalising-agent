"""AI runtime (step 8): route each AI task through the tenant's published runtime profile and approved deployments.

``run_task`` is the only way agent code calls a model. It resolves the profile (or the one a run pinned), plans
the route deterministically (``app.domain.ai_runtime``), tries at most two approved deployments with schema and
citation validation, then falls back to the task's deterministic template, and logs the invocation (append-only).
"""

import hashlib
import json
import uuid
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.adapters.local.ai import AnthropicProvider, HttpContractProvider
from app.adapters.registry import Platform
from app.config import get_settings
from app.db.models import AIInvocation, AISecret, Artifact, ArtifactVersion
from app.domain import ai_runtime as rt
from app.domain.artifacts import ArtifactKind, ArtifactStatus, Bump, format_ref, parse_ref
from app.domain.common import FieldError
from app.ports.ai import AIProvider, AIRequest
from app.ports.audit import AuditRecord
from app.ports.auth import Principal
from app.seed.ai import AI_RUNTIME_KEY
from app.services import artifacts as registry
from app.services.ai_runner import AITaskResult, CitationCheck, run_ai_chain
from app.services.errors import ServiceError

SECRET_NAME_RE = r"^[a-z0-9][a-z0-9-]{1,62}$"


class AIRuntimeError(ServiceError):
    code = "ai_runtime_error"


class AIRuntimeValidation(AIRuntimeError):
    status_code = 422
    code = "validation_failed"


class AIRuntimeConflict(AIRuntimeError):
    status_code = 409
    code = "conflict"


class AIRuntimeNotFound(AIRuntimeError):
    status_code = 404
    code = "not_found"


def _hash(value: Any) -> str:
    return "sha256:" + hashlib.sha256(json.dumps(value, sort_keys=True, default=str).encode()).hexdigest()


# ---------------------------------------------------------------------------
# Registry access
# ---------------------------------------------------------------------------

def _deployment(artifact: Artifact, v: ArtifactVersion) -> rt.Deployment:
    return rt.Deployment(key=artifact.key, ref=format_ref(artifact.key, registry.version_of(v)), digest=v.digest,
                         status=v.status, content=v.content)


def load_deployments(session: Session, tenant_id: str) -> dict[str, rt.Deployment]:
    """Every AI deployment: its selectable version, else its newest non-draft one (shown as not approved)."""
    out: dict[str, rt.Deployment] = {}
    arts = session.scalars(select(Artifact).where(Artifact.tenant_id == tenant_id,
                                                  Artifact.kind == ArtifactKind.AI_DEPLOYMENT.value))
    for a in arts:
        v = registry.selectable_version(session, a)
        if v is None:
            v = next((x for x in registry.list_versions(session, a) if x.status != ArtifactStatus.DRAFT), None)
        if v is not None and registry.verify_digest(a, v):
            d = _deployment(a, v)
            out[d.key] = out[d.ref] = d
    return out


def load_pinned_deployments(session: Session, tenant_id: str, refs: list[str]) -> dict[str, rt.Deployment]:
    out: dict[str, rt.Deployment] = {}
    for ref in refs:
        key, ver = parse_ref(ref)
        a, v = registry.resolve(session, tenant_id, key, ver, kind=ArtifactKind.AI_DEPLOYMENT)
        d = _deployment(a, v)
        out[d.key] = out[d.ref] = d
    return out


def current_profile(session: Session, tenant_id: str) -> tuple[Artifact, ArtifactVersion] | None:
    try:
        return registry.resolve(session, tenant_id, AI_RUNTIME_KEY, kind=ArtifactKind.AI_RUNTIME_PROFILE)
    except registry.ArtifactNotFound:
        return None


# ---------------------------------------------------------------------------
# Secrets
# ---------------------------------------------------------------------------

def get_secret(session: Session, platform: Platform, tenant_id: str, name: str) -> str | None:
    row = session.scalar(select(AISecret).where(AISecret.tenant_id == tenant_id, AISecret.name == name))
    return platform.secrets.decrypt(row.ciphertext) if row else None


def list_secrets(session: Session, tenant_id: str) -> list[AISecret]:
    return list(session.scalars(select(AISecret).where(AISecret.tenant_id == tenant_id).order_by(AISecret.name)))


def set_secret(session: Session, platform: Platform, principal: Principal, name: str, value: str) -> AISecret:
    import re

    errors = []
    if not re.match(SECRET_NAME_RE, name):
        errors.append(FieldError("name", "Use 2-63 lower-case letters, digits or hyphens"))
    if not value or len(value) > 4000:
        errors.append(FieldError("value", "Enter the secret (1-4000 characters)"))
    if errors:
        raise AIRuntimeValidation("The secret is not valid", errors)
    now = datetime.now(UTC)
    row = session.scalar(select(AISecret).where(AISecret.tenant_id == principal.tenant_id, AISecret.name == name)
                         .with_for_update())
    if row is None:
        row = AISecret(tenant_id=principal.tenant_id, name=name, ciphertext="", updated_by=principal.user_id,
                       updated_at=now)
        session.add(row)
    row.ciphertext = platform.secrets.encrypt(value)
    row.updated_by, row.updated_at = principal.user_id, now
    session.flush()
    _audit(session, platform, principal, "ai.secret_set", "ai_secret", name, {"name": name})
    session.commit()
    return row


def delete_secret(session: Session, platform: Platform, principal: Principal, name: str) -> None:
    row = session.scalar(select(AISecret).where(AISecret.tenant_id == principal.tenant_id, AISecret.name == name))
    if row is None:
        raise AIRuntimeNotFound(f"No secret {name}")
    session.delete(row)
    _audit(session, platform, principal, "ai.secret_deleted", "ai_secret", name, {"name": name})
    session.commit()


def _audit(session: Session, platform: Platform, principal: Principal, action: str, entity_type: str,
           entity_id: str, payload: dict[str, Any]) -> None:
    platform.audit.record(AuditRecord(tenant_id=principal.tenant_id, actor_id=principal.user_id,
                                      actor_roles=principal.roles, auth_context=principal.auth_context,
                                      action=action, entity_type=entity_type, entity_id=entity_id, payload=payload),
                          session=session)


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

ProviderBuilder = Callable[[rt.Deployment, str | None, Platform], AIProvider]


def _platform(dep: rt.Deployment, secret: str | None, platform: Platform) -> AIProvider:
    return platform.ai


def _anthropic(dep: rt.Deployment, secret: str | None, platform: Platform) -> AIProvider:
    key = secret or get_settings().anthropic_api_key
    if not key:
        raise LookupError(f"secret {dep.content.get('secretRef')} is not set")
    return AnthropicProvider(key, dep.content["model"])


def _byo(dep: rt.Deployment, secret: str | None, platform: Platform) -> AIProvider:
    if dep.content.get("secretRef") and not secret:
        raise LookupError(f"secret {dep.content['secretRef']} is not set")
    return HttpContractProvider(dep.content["endpoint"], secret, dep.content.get("model"))


BUILDERS: dict[str, ProviderBuilder] = {"platform": _platform, "anthropic": _anthropic, "byo_http": _byo}


# ---------------------------------------------------------------------------
# Planning and execution
# ---------------------------------------------------------------------------

@dataclass
class Runtime:
    profile_ref: str | None
    profile_digest: str | None
    profile: dict[str, Any] | None
    deployments: dict[str, rt.Deployment]


def runtime_for(session: Session, tenant_id: str, pinned: dict[str, Any] | None = None) -> Runtime:
    """The live runtime, or the one a run pinned (``{"profile": {"ref"}, "routes": {task: [{"ref"}]}}``)."""
    if pinned and pinned.get("profile"):
        key, ver = parse_ref(pinned["profile"]["ref"])
        a, v = registry.resolve(session, tenant_id, key, ver, kind=ArtifactKind.AI_RUNTIME_PROFILE)
        refs = sorted({d["ref"] for chain in pinned.get("routes", {}).values() for d in chain})
        return Runtime(format_ref(a.key, registry.version_of(v)), v.digest, v.content,
                       load_pinned_deployments(session, tenant_id, refs))
    found = current_profile(session, tenant_id)
    deployments = load_deployments(session, tenant_id)
    if found is None:
        return Runtime(None, None, None, deployments)
    a, v = found
    return Runtime(format_ref(a.key, registry.version_of(v)), v.digest, v.content, deployments)


def plans(session: Session, tenant_id: str) -> tuple[Runtime, list[rt.RoutePlan]]:
    r = runtime_for(session, tenant_id)
    if r.profile is None:
        return r, []
    return r, rt.plan_all(r.profile, r.deployments, registry.today())


def pin(session: Session, tenant_id: str) -> dict[str, Any]:
    """What a run freezes into its manifest (step 9): profile ref + digest and each task's deployment refs."""
    r, ps = plans(session, tenant_id)
    if r.profile is None:
        return {"profile": None, "routes": {}}
    return {"profile": {"ref": r.profile_ref, "digest": r.profile_digest}, "routes": rt.pinned_routes(ps)}


def run_task(session: Session, platform: Platform, tenant_id: str, actor_id: str, task: rt.Task,
             request: AIRequest, *, fallback: Callable[[], dict[str, Any]],
             citation_check: CitationCheck | None = None, run_id: uuid.UUID | None = None,
             pinned: dict[str, Any] | None = None) -> AITaskResult:
    r = runtime_for(session, tenant_id, pinned)
    providers: list[tuple[str, AIProvider]] = []
    errors: list[str] = []
    max_retries = get_settings().ai_max_retries
    treatment = rt.FailureTreatment.DETERMINISTIC_FALLBACK
    if r.profile is None:  # no runtime profile published yet: the platform's configured model
        providers.append(("platform", platform.ai))
    else:
        max_retries = r.profile.get("maxRetries", max_retries)
        treatment = rt.FailureTreatment(r.profile["failureTreatment"])
        if pinned and pinned.get("routes") is not None:
            chain = [r.deployments[d["ref"]] for d in pinned["routes"].get(task.value, [])]
        else:
            chain = rt.plan_route(r.profile, task.value, r.deployments, registry.today()).chain
        for dep in chain:
            builder = BUILDERS.get(dep.content["provider"])
            secret_name = dep.content.get("secretRef")
            try:
                secret = get_secret(session, platform, tenant_id, secret_name) if secret_name else None
                if builder is None:
                    raise LookupError(f"no adapter for provider {dep.content['provider']}")
                providers.append((dep.ref, builder(dep, secret, platform)))
            except Exception as exc:  # noqa: BLE001 - a broken deployment must never break the task
                errors.append(f"{dep.ref}: {exc}")
    request = replace(request, task=task.value)
    result = run_ai_chain(providers, request, fallback=fallback, citation_check=citation_check,
                          max_retries=max_retries)
    result = replace(result, errors=errors + result.errors,
                     needs_human_review=result.source == "fallback" and bool(providers)
                     and treatment is rt.FailureTreatment.HUMAN_REVIEW)
    session.add(AIInvocation(
        tenant_id=tenant_id, run_id=run_id, task=task.value, profile_ref=r.profile_ref,
        deployment_ref=result.deployment, model=result.model, source=result.source, attempts=result.attempts,
        chain=[label for label, _ in providers], errors=result.errors[:20], needs_human_review=result.needs_human_review,
        request_hash=_hash({"system": request.system, "prompt": request.prompt, "schema": request.schema}),
        output_hash=_hash(result.output), actor_id=actor_id))
    session.flush()
    return result


# ---------------------------------------------------------------------------
# Changing the profile (AI Runtime screen)
# ---------------------------------------------------------------------------

def save_profile(session: Session, platform: Platform, principal: Principal, expected_ref: str,
                 changes: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    """Publish a new version of the runtime profile with ``changes`` applied (strategy, routes, ...)."""
    found = current_profile(session, principal.tenant_id)
    if found is None:
        raise AIRuntimeNotFound(f"No published {AI_RUNTIME_KEY}; run python -m app.cli seed-artifacts")
    artifact, current = found
    current_ref = format_ref(artifact.key, registry.version_of(current))
    if expected_ref != current_ref:
        raise AIRuntimeConflict(f"The AI runtime changed meanwhile (now {current_ref}); reload and try again")
    content = {**current.content, **{k: v for k, v in changes.items() if v is not None}}
    deployments = load_deployments(session, principal.tenant_id)
    errors = []
    for i, route in enumerate(content.get("routes", [])):
        for f in ("primary", "fallback"):
            if route.get(f) and rt._lookup(route[f], deployments) is None:
                errors.append(FieldError(f"routes[{i}].{f}", f"No AI deployment {route[f]} in the registry"))
    if not errors and not rt.check_ai_runtime_profile(content):
        for i, plan in enumerate(rt.plan_all(content, deployments, registry.today())):
            if not plan.compliant:  # fail closed: never publish a model route that no approved deployment can serve
                failed = next(c for c in plan.checks if c.status == "FAIL")
                errors.append(FieldError(f"routes.{plan.task}", f"{plan.label}: {failed.message}. Choose an "
                                         "eligible deployment or set the task to deterministic."))
    if errors:
        raise AIRuntimeValidation("The AI runtime is not valid", errors)
    if content == current.content:
        return current_ref, content
    kind = ArtifactKind.AI_RUNTIME_PROFILE
    _, draft = registry.clone_version(session, platform.audit, principal, kind, artifact.id,
                                      registry.version_of(current), Bump.MINOR, "Changed on the AI Runtime screen")
    try:
        _, draft = registry.update_draft(session, platform.audit, principal, kind, artifact.id,
                                         registry.version_of(draft), {"content": content}, draft.lock_version)
        _, published = registry.publish(session, platform.audit, principal, kind, artifact.id,
                                        registry.version_of(draft), draft.lock_version)
    except ServiceError:
        session.rollback()
        draft = session.get(ArtifactVersion, draft.id)
        if draft is not None and draft.status == ArtifactStatus.DRAFT:
            registry.discard_draft(session, platform.audit, principal, kind, artifact.id,
                                   registry.version_of(draft), draft.lock_version)
        raise
    return format_ref(artifact.key, registry.version_of(published)), published.content


def invocations(session: Session, tenant_id: str, *, run_id: uuid.UUID | None = None,
                limit: int = 20) -> list[AIInvocation]:
    stmt = select(AIInvocation).where(AIInvocation.tenant_id == tenant_id)
    if run_id is not None:
        stmt = stmt.where(AIInvocation.run_id == run_id)
    return list(session.scalars(stmt.order_by(AIInvocation.created_at.desc()).limit(limit)))
