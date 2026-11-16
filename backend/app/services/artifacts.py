"""Artifact registry service: create, edit drafts, clone, validate, publish, deprecate, retire, resolve.

Rules live in ``app.domain.artifacts`` / ``app.domain.artifact_content``; the database repeats the
immutability rules with triggers (migration 0004). Every change is audited and emits a domain event
through the outbox in the same transaction.
"""

import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, or_, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Artifact, ArtifactVersion
from app.domain.artifact_content import canonical_schema_key, validate_content
from app.domain.artifacts import (
    FIRST_VERSION,
    REASON_REQUIRED,
    ArtifactKind,
    ArtifactStatus,
    Bump,
    CanonicalJsonError,
    Version,
    can_transition,
    canonical_json,
    compute_digest,
    format_ref,
    is_selectable,
    key_error,
    next_version,
)
from app.domain.common import FieldError
from app.ports.audit import AuditLog, AuditRecord
from app.ports.auth import Principal
from app.services.errors import ServiceError
from app.services.outbox import emit

NAME_MIN, NAME_MAX = 3, 120
DESCRIPTION_MAX = 2000
NOTE_MAX = 2000
REASON_MIN, REASON_MAX = 3, 2000
MAX_CONTENT_BYTES = 5 * 1024 * 1024

S = ArtifactStatus


class ArtifactError(ServiceError):
    code = "artifact_error"


class ArtifactValidationError(ArtifactError):
    status_code = 422
    code = "validation_failed"


class ArtifactKeyTaken(ArtifactError):
    status_code = 409
    code = "key_taken"


class ArtifactNotFound(ArtifactError):
    status_code = 404
    code = "not_found"


class ArtifactVersionConflict(ArtifactError):
    status_code = 409
    code = "version_conflict"


class ArtifactNotEditable(ArtifactError):
    status_code = 409
    code = "not_editable"


class ArtifactInvalidTransition(ArtifactError):
    status_code = 409
    code = "invalid_transition"


class ArtifactDraftExists(ArtifactError):
    status_code = 409
    code = "draft_exists"


class ArtifactIntegrityError(ArtifactError):
    status_code = 500
    code = "integrity_failed"


def today() -> date:
    return datetime.now(UTC).date()


def version_of(v: ArtifactVersion) -> Version:
    return Version(v.version_major, v.version_minor)


# ---------------------------------------------------------------------------
# Input checks
# ---------------------------------------------------------------------------

def _check_content(content: Any) -> list[FieldError]:
    if not isinstance(content, dict):
        return [FieldError("content", "Content must be a JSON object")]
    try:
        size = len(canonical_json(content))
    except CanonicalJsonError as exc:
        return [FieldError("content", str(exc))]
    if size > MAX_CONTENT_BYTES:
        return [FieldError("content", f"Content is larger than {MAX_CONTENT_BYTES // (1024 * 1024)} MB")]
    return []


def _check_validity(valid_from: date | None, valid_to: date | None) -> list[FieldError]:
    if valid_from and valid_to and valid_from > valid_to:
        return [FieldError("valid_to", "Valid to must be on or after valid from")]
    return []


def _check_text(field: str, value: str | None, maximum: int) -> list[FieldError]:
    if value is not None and len(value) > maximum:
        return [FieldError(field, f"At most {maximum} characters")]
    return []


def _raise_if(errors: list[FieldError], message: str = "Artifact is not valid") -> None:
    if errors:
        raise ArtifactValidationError(message, errors)


# ---------------------------------------------------------------------------
# Audit + events
# ---------------------------------------------------------------------------

def _describe(artifact: Artifact, v: ArtifactVersion) -> dict[str, Any]:
    return {"artifact_id": str(artifact.id), "version_id": str(v.id), "kind": artifact.kind,
            "key": artifact.key, "version": str(version_of(v)), "ref": format_ref(artifact.key, version_of(v)),
            "digest": v.digest, "status": v.status}


def _record(session: Session, audit: AuditLog, principal: Principal, action: str, event_type: str,
            artifact: Artifact, v: ArtifactVersion, extra: dict[str, Any] | None = None) -> None:
    payload = _describe(artifact, v) | (extra or {})
    audit.record(AuditRecord(
        tenant_id=principal.tenant_id, actor_id=principal.user_id, actor_roles=principal.roles,
        auth_context=principal.auth_context, action=action, entity_type="artifact_version",
        entity_id=str(v.id), payload=payload), session=session)
    emit(session, tenant_id=principal.tenant_id, event_type=event_type, aggregate_type="artifact",
         aggregate_id=str(artifact.id), payload=payload)


# ---------------------------------------------------------------------------
# Reads
# ---------------------------------------------------------------------------

def get_artifact(session: Session, tenant_id: str, kind: ArtifactKind, artifact_id: uuid.UUID, *,
                 for_update: bool = False) -> Artifact:
    stmt = select(Artifact).where(Artifact.id == artifact_id, Artifact.tenant_id == tenant_id,
                                  Artifact.kind == kind.value)
    if for_update:
        stmt = stmt.with_for_update()
    artifact = session.scalar(stmt)
    if artifact is None:
        raise ArtifactNotFound("Artifact not found")
    return artifact


def list_versions(session: Session, artifact: Artifact) -> list[ArtifactVersion]:
    return list(session.scalars(
        select(ArtifactVersion).where(ArtifactVersion.artifact_id == artifact.id,
                                      ArtifactVersion.tenant_id == artifact.tenant_id)
        .order_by(ArtifactVersion.version_major.desc(), ArtifactVersion.version_minor.desc())))


def get_version(session: Session, artifact: Artifact, version: Version, *,
                for_update: bool = False) -> ArtifactVersion:
    stmt = select(ArtifactVersion).where(
        ArtifactVersion.artifact_id == artifact.id, ArtifactVersion.tenant_id == artifact.tenant_id,
        ArtifactVersion.version_major == version.major, ArtifactVersion.version_minor == version.minor)
    if for_update:
        stmt = stmt.with_for_update()
    v = session.scalar(stmt)
    if v is None:
        raise ArtifactNotFound(f"Version {version} of {artifact.key} not found")
    return v


@dataclass(frozen=True)
class ArtifactListRow:
    artifact: Artifact
    published: ArtifactVersion | None
    latest: ArtifactVersion


def list_artifacts(session: Session, tenant_id: str, kind: ArtifactKind, *, query: str = "",
                   limit: int = 50, offset: int = 0) -> tuple[list[ArtifactListRow], int]:
    stmt = select(Artifact).where(Artifact.tenant_id == tenant_id, Artifact.kind == kind.value)
    q = query.strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Artifact.key.ilike(like), Artifact.name.ilike(like)))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    artifacts = list(session.scalars(stmt.order_by(Artifact.key).limit(limit).offset(offset)))
    if not artifacts:
        return [], total
    versions = session.scalars(
        select(ArtifactVersion).where(ArtifactVersion.tenant_id == tenant_id,
                                      ArtifactVersion.artifact_id.in_([a.id for a in artifacts]))
        .order_by(ArtifactVersion.version_major.desc(), ArtifactVersion.version_minor.desc()))
    latest: dict[uuid.UUID, ArtifactVersion] = {}
    published: dict[uuid.UUID, ArtifactVersion] = {}
    for v in versions:
        latest.setdefault(v.artifact_id, v)
        if v.status == S.PUBLISHED:
            published[v.artifact_id] = v
    return [ArtifactListRow(a, published.get(a.id), latest[a.id]) for a in artifacts], total


def verify_digest(artifact: Artifact, v: ArtifactVersion) -> bool:
    return compute_digest(artifact.kind, artifact.key, v.content) == v.digest


def _artifact_by_key(session: Session, tenant_id: str, key: str) -> Artifact | None:
    return session.scalar(select(Artifact).where(Artifact.tenant_id == tenant_id, Artifact.key == key))


def selectable_version(session: Session, artifact: Artifact, on: date | None = None) -> ArtifactVersion | None:
    v = session.scalar(select(ArtifactVersion).where(
        ArtifactVersion.artifact_id == artifact.id, ArtifactVersion.tenant_id == artifact.tenant_id,
        ArtifactVersion.status == S.PUBLISHED))
    if v is None or not is_selectable(v.status, v.valid_from, v.valid_to, on or today()):
        return None
    return v


def resolve(session: Session, tenant_id: str, key: str, version: Version | None = None, *,
            kind: ArtifactKind | None = None, on: date | None = None) -> tuple[Artifact, ArtifactVersion]:
    """``KEY`` -> the version new runs may select today; ``KEY@1.0`` -> exactly that version
    (any status except DRAFT, so runs can re-read what they pinned). The digest is re-checked."""
    artifact = _artifact_by_key(session, tenant_id, key)
    if artifact is None or (kind is not None and artifact.kind != kind.value):
        raise ArtifactNotFound(f"No artifact {key}")
    if version is None:
        v = selectable_version(session, artifact, on)
        if v is None:
            raise ArtifactNotFound(f"{key} has no published version that is valid today")
    else:
        v = get_version(session, artifact, version)
        if v.status == S.DRAFT:
            raise ArtifactNotFound(f"{format_ref(key, version)} is a draft and cannot be referenced")
    if not verify_digest(artifact, v):
        raise ArtifactIntegrityError(f"{format_ref(key, version_of(v))}: stored content does not match its digest")
    return artifact, v


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_version(session: Session, artifact: Artifact, v: ArtifactVersion) -> list[FieldError]:
    """Blocking problems that prevent publishing (structure, meaning, cross-references, dates)."""
    errors = validate_content(artifact.kind, v.content,
                              canonical=_canonical_content(session, artifact.tenant_id, artifact.kind, v.content))
    if v.valid_to and v.valid_to < today():
        errors.append(FieldError("valid_to", "Valid to is in the past; the version could never be used"))
    return errors


def _canonical_content(session: Session, tenant_id: str, kind: str, content: Any) -> dict | None:
    key = canonical_schema_key(kind, content)
    if key is None:
        return None
    artifact = _artifact_by_key(session, tenant_id, key)
    if artifact is None or artifact.kind != ArtifactKind.CANONICAL_SCHEMA.value:
        return None
    v = selectable_version(session, artifact)
    return v.content if v else None


# ---------------------------------------------------------------------------
# Writes
# ---------------------------------------------------------------------------

def create_artifact(session: Session, audit: AuditLog, principal: Principal, kind: ArtifactKind, *,
                    key: str, name: str, content: Any, description: str | None = None,
                    valid_from: date | None = None, valid_to: date | None = None,
                    change_note: str | None = None) -> tuple[Artifact, ArtifactVersion]:
    """New artifact with draft version 1.0."""
    tenant = principal.tenant_id
    key = (key or "").strip()
    name = " ".join((name or "").split())
    errors: list[FieldError] = []
    if msg := key_error(key):
        errors.append(FieldError("key", msg))
    if not NAME_MIN <= len(name) <= NAME_MAX:
        errors.append(FieldError("name", f"Name must be {NAME_MIN}–{NAME_MAX} characters"))
    errors += _check_text("description", description, DESCRIPTION_MAX)
    errors += _check_text("change_note", change_note, NOTE_MAX)
    errors += _check_content(content) + _check_validity(valid_from, valid_to)
    _raise_if(errors)
    if _artifact_by_key(session, tenant, key) is not None:
        raise ArtifactKeyTaken("Key already used", [FieldError("key", f"An artifact with key {key} already exists")])

    artifact = Artifact(tenant_id=tenant, kind=kind.value, key=key, name=name, description=description,
                        created_by=principal.user_id)
    session.add(artifact)
    try:
        session.flush()
    except IntegrityError as exc:  # concurrent create with the same key
        session.rollback()
        raise ArtifactKeyTaken("Key already used",
                               [FieldError("key", f"An artifact with key {key} already exists")]) from exc
    v = ArtifactVersion(
        tenant_id=tenant, artifact_id=artifact.id, kind=kind.value, version_major=FIRST_VERSION.major,
        version_minor=FIRST_VERSION.minor, status=S.DRAFT.value, content=content,
        digest=compute_digest(kind, key, content), valid_from=valid_from, valid_to=valid_to,
        change_note=change_note, created_by=principal.user_id, lock_version=1)
    session.add(v)
    session.flush()
    _record(session, audit, principal, "artifact.created", "ArtifactVersionCreated", artifact, v,
            {"name": name})
    session.commit()
    return artifact, v


def _check_lock(v: ArtifactVersion, expected: int) -> None:
    if v.lock_version != expected:
        raise ArtifactVersionConflict("This version was changed by someone else. Reload and try again.")


def _touch(v: ArtifactVersion) -> None:
    v.lock_version += 1
    v.updated_at = datetime.now(UTC)


DRAFT_FIELDS = ("content", "valid_from", "valid_to", "change_note")


def update_draft(session: Session, audit: AuditLog, principal: Principal, kind: ArtifactKind,
                 artifact_id: uuid.UUID, version: Version, changes: dict[str, Any],
                 expected_lock: int) -> tuple[Artifact, ArtifactVersion]:
    """Save a draft. Content need not be valid yet (``validate`` / ``publish`` check it)."""
    artifact = get_artifact(session, principal.tenant_id, kind, artifact_id)
    v = get_version(session, artifact, version, for_update=True)
    if v.status != S.DRAFT:
        raise ArtifactNotEditable(f"{format_ref(artifact.key, version)} is {v.status}; clone it to make a change")
    _check_lock(v, expected_lock)
    changes = {k: changes[k] for k in DRAFT_FIELDS if k in changes}
    errors: list[FieldError] = []
    if "content" in changes:
        errors += _check_content(changes["content"])
    errors += _check_text("change_note", changes.get("change_note"), NOTE_MAX)
    errors += _check_validity(changes.get("valid_from", v.valid_from), changes.get("valid_to", v.valid_to))
    _raise_if(errors)

    before_digest = v.digest
    changed = sorted(k for k, val in changes.items() if getattr(v, k) != val)
    if not changed:
        session.rollback()
        return artifact, get_version(session, artifact, version)
    for k in changed:
        setattr(v, k, changes[k])
    v.digest = compute_digest(kind, artifact.key, v.content)
    _touch(v)
    session.flush()
    audit.record(AuditRecord(
        tenant_id=principal.tenant_id, actor_id=principal.user_id, actor_roles=principal.roles,
        auth_context=principal.auth_context, action="artifact.draft_updated", entity_type="artifact_version",
        entity_id=str(v.id), payload=_describe(artifact, v) | {"changed_fields": changed,
                                                               "previous_digest": before_digest}),
        session=session)
    session.commit()
    return artifact, v


def clone_version(session: Session, audit: AuditLog, principal: Principal, kind: ArtifactKind,
                  artifact_id: uuid.UUID, source: Version, bump: Bump,
                  change_note: str | None = None) -> tuple[Artifact, ArtifactVersion]:
    """New DRAFT copied from ``source``, numbered after the highest existing version."""
    _raise_if(_check_text("change_note", change_note, NOTE_MAX))
    artifact = get_artifact(session, principal.tenant_id, kind, artifact_id, for_update=True)
    src = get_version(session, artifact, source)
    versions = list_versions(session, artifact)
    draft = next((x for x in versions if x.status == S.DRAFT), None)
    if draft is not None:
        raise ArtifactDraftExists(f"{format_ref(artifact.key, version_of(draft))} is already a draft; "
                                  "publish or discard it first")
    new = next_version(version_of(versions[0]), bump)
    v = ArtifactVersion(
        tenant_id=artifact.tenant_id, artifact_id=artifact.id, kind=artifact.kind, version_major=new.major,
        version_minor=new.minor, parent_version_id=src.id, status=S.DRAFT.value, content=src.content,
        digest=compute_digest(kind, artifact.key, src.content), valid_from=src.valid_from,
        valid_to=src.valid_to, change_note=change_note, created_by=principal.user_id, lock_version=1)
    session.add(v)
    session.flush()
    _record(session, audit, principal, "artifact.version_created", "ArtifactVersionCreated", artifact, v,
            {"parent_version": str(source), "bump": bump.value})
    session.commit()
    return artifact, v


def publish(session: Session, audit: AuditLog, principal: Principal, kind: ArtifactKind,
            artifact_id: uuid.UUID, version: Version, expected_lock: int) -> tuple[Artifact, ArtifactVersion]:
    """Validate and publish a DRAFT; the previous PUBLISHED version becomes SUPERSEDED."""
    artifact = get_artifact(session, principal.tenant_id, kind, artifact_id, for_update=True)
    v = get_version(session, artifact, version, for_update=True)
    if v.status != S.DRAFT:
        raise ArtifactInvalidTransition(f"Only a draft can be published ({format_ref(artifact.key, version)} "
                                        f"is {v.status})")
    _check_lock(v, expected_lock)
    if not verify_digest(artifact, v):
        raise ArtifactIntegrityError("Stored content does not match its digest")
    _raise_if(validate_version(session, artifact, v), "The draft has blocking problems")

    now = datetime.now(UTC)
    previous = session.scalar(select(ArtifactVersion).where(
        ArtifactVersion.artifact_id == artifact.id, ArtifactVersion.status == S.PUBLISHED).with_for_update())
    if previous is not None:
        previous.status = S.SUPERSEDED.value
        previous.status_changed_at, previous.status_changed_by = now, principal.user_id
        previous.status_reason = f"Superseded by {version}"
        _touch(previous)
        session.flush()  # free the one-PUBLISHED-per-artifact slot before publishing

    v.status = S.PUBLISHED.value
    v.published_at, v.published_by = now, principal.user_id
    v.status_changed_at, v.status_changed_by = now, principal.user_id
    _touch(v)
    session.flush()
    _record(session, audit, principal, "artifact.published", "ArtifactVersionPublished", artifact, v,
            {"supersedes": str(version_of(previous)) if previous else None})
    if previous is not None:
        _record(session, audit, principal, "artifact.superseded", "ArtifactVersionSuperseded", artifact,
                previous, {"superseded_by": str(version)})
    session.commit()
    return artifact, v


def change_status(session: Session, audit: AuditLog, principal: Principal, kind: ArtifactKind,
                  artifact_id: uuid.UUID, version: Version, target: ArtifactStatus, expected_lock: int,
                  reason: str) -> tuple[Artifact, ArtifactVersion]:
    """Deprecate or retire a version (publishing has its own function). A reason is required."""
    if target not in (S.DEPRECATED, S.RETIRED):
        raise ArtifactInvalidTransition(f"Use publish to make a version {target}")
    reason = " ".join((reason or "").split())
    if target in REASON_REQUIRED and not REASON_MIN <= len(reason) <= REASON_MAX:
        raise ArtifactValidationError("A reason is required",
                                      [FieldError("reason", f"Give a reason ({REASON_MIN}–{REASON_MAX} characters)")])
    artifact = get_artifact(session, principal.tenant_id, kind, artifact_id, for_update=True)
    v = get_version(session, artifact, version, for_update=True)
    if not can_transition(v.status, target):
        raise ArtifactInvalidTransition(f"{format_ref(artifact.key, version)} is {v.status} and cannot become {target}")
    _check_lock(v, expected_lock)
    previous_status = v.status
    v.status = target.value
    v.status_changed_at, v.status_changed_by, v.status_reason = datetime.now(UTC), principal.user_id, reason
    _touch(v)
    session.flush()
    action, event = {S.DEPRECATED: ("artifact.deprecated", "ArtifactVersionDeprecated"),
                     S.RETIRED: ("artifact.retired", "ArtifactVersionRetired")}[target]
    _record(session, audit, principal, action, event, artifact, v,
            {"previous_status": previous_status, "reason": reason})
    session.commit()
    return artifact, v


def discard_draft(session: Session, audit: AuditLog, principal: Principal, kind: ArtifactKind,
                  artifact_id: uuid.UUID, version: Version, expected_lock: int) -> bool:
    """Delete a DRAFT. If it was the artifact's only version, the artifact goes too. Returns that."""
    artifact = get_artifact(session, principal.tenant_id, kind, artifact_id, for_update=True)
    v = get_version(session, artifact, version, for_update=True)
    if v.status != S.DRAFT:
        raise ArtifactNotEditable(f"{format_ref(artifact.key, version)} is {v.status}; only drafts can be discarded")
    _check_lock(v, expected_lock)
    _record(session, audit, principal, "artifact.draft_discarded", "ArtifactVersionDiscarded", artifact, v)
    session.delete(v)
    session.flush()
    remaining = session.scalar(select(func.count()).select_from(ArtifactVersion)
                               .where(ArtifactVersion.artifact_id == artifact.id)) or 0
    if remaining == 0:
        session.delete(artifact)
    session.commit()
    return remaining == 0
