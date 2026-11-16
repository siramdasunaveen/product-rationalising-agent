"""Seed the registries of a tenant with TELCO-CANONICAL@1.0 and the starter lookups and transforms.

Idempotent: artifacts whose key already exists are left alone (change them by publishing new versions).
"""

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Artifact
from app.domain.artifacts import FIRST_VERSION, ArtifactKind
from app.ports.audit import AuditLog
from app.ports.auth import Principal
from app.seed.telco import CANONICAL_KEY, LOOKUPS, TELCO_CANONICAL, TRANSFORMS
from app.services.artifacts import create_artifact, publish

SEED_ACTOR = "system:seed"


def seed_principal(tenant_id: str) -> Principal:
    return Principal(user_id=SEED_ACTOR, tenant_id=tenant_id, name="System seed", roles=("system",),
                     permissions=frozenset(), auth_context={"source": "cli"})


def seed_items() -> list[tuple[ArtifactKind, str, str, dict]]:
    items = [(ArtifactKind.CANONICAL_SCHEMA, CANONICAL_KEY, "Telco canonical model", TELCO_CANONICAL)]
    items += [(ArtifactKind.LOOKUP, key, name, content) for key, name, content in LOOKUPS]
    items += [(ArtifactKind.TRANSFORM, key, name, content) for key, name, content in TRANSFORMS]
    return items


def seed_artifacts(session: Session, audit: AuditLog, tenant_id: str) -> dict[str, list[str]]:
    """Create and publish each seed artifact that does not exist yet. Canonical schema goes first
    because lookups are checked against it."""
    principal = seed_principal(tenant_id)
    result: dict[str, list[str]] = {"created": [], "existing": []}
    for kind, key, name, content in seed_items():
        exists = session.scalar(select(Artifact.id).where(Artifact.tenant_id == tenant_id, Artifact.key == key))
        if exists:
            result["existing"].append(key)
            continue
        artifact, v = create_artifact(session, audit, principal, kind, key=key, name=name, content=content,
                                      description=content.get("description"),
                                      change_note="Initial version (seed)")
        publish(session, audit, principal, kind, artifact.id, FIRST_VERSION, v.lock_version)
        result["created"].append(f"{key}@{FIRST_VERSION}")
    return result
