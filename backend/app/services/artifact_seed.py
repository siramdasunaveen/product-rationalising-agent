"""Seed the registries of a tenant with TELCO-CANONICAL@1.0, starter lookups and transforms, and
the governed Excel templates of the evidence domains.

Idempotent. Artifacts that do not exist are created and published. Artifacts that exist are upgraded only
when their published version was itself published by the seed and the seed content has changed: the
seed then clones it as a new minor version and publishes that (the old one becomes SUPERSEDED, so runs
that pinned it keep it). Artifacts a person has changed, or with a draft open, are never touched.
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Artifact, ArtifactVersion
from app.domain.artifacts import FIRST_VERSION, ArtifactKind, ArtifactStatus, Bump, format_ref
from app.ports.audit import AuditLog
from app.ports.auth import Principal
from app.seed.ai import AI_DEPLOYMENTS, AI_RUNTIME_KEY, TELCO_AI_RUNTIME
from app.seed.telco import (
    CANONICAL_KEY,
    LOOKUPS,
    PRECEDENCE_KEY,
    READINESS_KEY,
    TELCO_CANONICAL,
    TELCO_PRECEDENCE,
    TELCO_READINESS,
    TEMPLATES,
    TRANSFORMS,
)
from app.services.artifacts import clone_version, create_artifact, list_versions, publish, update_draft, version_of

SEED_ACTOR = "system:seed"


def seed_principal(tenant_id: str) -> Principal:
    return Principal(user_id=SEED_ACTOR, tenant_id=tenant_id, name="System seed", roles=("system",),
                     permissions=frozenset(), auth_context={"source": "cli"})


def seed_items() -> list[tuple[ArtifactKind, str, str, dict]]:
    items = [(ArtifactKind.CANONICAL_SCHEMA, CANONICAL_KEY, "Telco canonical model", TELCO_CANONICAL)]
    items += [(ArtifactKind.LOOKUP, key, name, content) for key, name, content in LOOKUPS]
    items += [(ArtifactKind.TRANSFORM, key, name, content) for key, name, content in TRANSFORMS]
    items += [(ArtifactKind.EVIDENCE_TEMPLATE, key, name, content) for key, name, content in TEMPLATES]
    items.append((ArtifactKind.PRECEDENCE_POLICY, PRECEDENCE_KEY, "Telco reconciliation precedence", TELCO_PRECEDENCE))
    items.append((ArtifactKind.READINESS_POLICY, READINESS_KEY, "Telco evidence readiness", TELCO_READINESS))
    items += [(ArtifactKind.AI_DEPLOYMENT, key, name, content) for key, name, content in AI_DEPLOYMENTS]
    items.append((ArtifactKind.AI_RUNTIME_PROFILE, AI_RUNTIME_KEY, "Telco AI runtime", TELCO_AI_RUNTIME))
    return items


def _upgrade(session: Session, audit: AuditLog, principal: Principal, kind: ArtifactKind, artifact: Artifact,
             content: dict) -> str | None:
    versions = list_versions(session, artifact)
    if any(v.status == ArtifactStatus.DRAFT for v in versions):
        return None
    published: ArtifactVersion | None = next((v for v in versions if v.status == ArtifactStatus.PUBLISHED), None)
    if published is None or published.published_by != SEED_ACTOR or published.content == content:
        return None
    if versions[0].id != published.id:  # a person published something newer and retired/deprecated it
        return None
    _, draft = clone_version(session, audit, principal, kind, artifact.id, version_of(published), Bump.MINOR,
                             "Seed content updated")
    _, draft = update_draft(session, audit, principal, kind, artifact.id, version_of(draft),
                            {"content": content}, draft.lock_version)
    publish(session, audit, principal, kind, artifact.id, version_of(draft), draft.lock_version)
    return format_ref(artifact.key, version_of(draft))


def seed_artifacts(session: Session, audit: AuditLog, tenant_id: str) -> dict[str, list[str]]:
    """Create/upgrade the seed artifacts. Canonical schema goes first because others are checked against it."""
    principal = seed_principal(tenant_id)
    result: dict[str, list[str]] = {"created": [], "upgraded": [], "existing": []}
    for kind, key, name, content in seed_items():
        artifact = session.scalar(select(Artifact).where(Artifact.tenant_id == tenant_id, Artifact.key == key))
        if artifact is not None:
            ref = _upgrade(session, audit, principal, kind, artifact, content)
            result["upgraded" if ref else "existing"].append(ref or key)
            continue
        artifact, v = create_artifact(session, audit, principal, kind, key=key, name=name, content=content,
                                      description=content.get("description"),
                                      change_note="Initial version (seed)")
        publish(session, audit, principal, kind, artifact.id, FIRST_VERSION, v.lock_version)
        result["created"].append(f"{key}@{FIRST_VERSION}")
    return result
