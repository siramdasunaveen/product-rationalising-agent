"""Versioned artifact registries: canonical schemas, lookups, transforms, crosswalks (BUILD_PLAN step 3).

Lifecycle: DRAFT -> PUBLISHED -> DEPRECATED / SUPERSEDED / RETIRED. Only drafts change; a change to a
published version is a clone (new version). Runs pin ``KEY@major.minor`` plus the digest.
"""

import uuid
from enum import StrEnum
from typing import Annotated

from fastapi import APIRouter, Depends, Path, Query, status

from app.api.deps import DbDep, PlatformDep, require_permission
from app.api.schemas import (
    ArtifactCloneIn,
    ArtifactCreateIn,
    ArtifactDraftPatch,
    ArtifactListItem,
    ArtifactListOut,
    ArtifactLockIn,
    ArtifactOut,
    ArtifactRefOut,
    ArtifactStatusIn,
    ArtifactValidationOut,
    ArtifactVersionOut,
    ArtifactVersionSummary,
    ErrorResponse,
    FieldErrorOut,
)
from app.db.models import Artifact, ArtifactVersion
from app.domain.artifact_content import json_schema_for
from app.domain.artifacts import ArtifactKind, ArtifactStatus, Bump, Version, format_ref, is_selectable, parse_ref
from app.domain.common import FieldError
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import artifacts as service

router = APIRouter(prefix="/registries", tags=["registries"])


class RegistryKind(StrEnum):
    CANONICAL_SCHEMAS = "canonical-schemas"
    LOOKUPS = "lookups"
    TRANSFORMS = "transforms"
    CROSSWALKS = "crosswalks"
    EVIDENCE_TEMPLATES = "evidence-templates"
    PRECEDENCE_POLICIES = "precedence-policies"
    READINESS_POLICIES = "readiness-policies"
    AI_DEPLOYMENTS = "ai-deployments"
    AI_RUNTIME_PROFILES = "ai-runtime-profiles"


KINDS = {
    RegistryKind.CANONICAL_SCHEMAS: ArtifactKind.CANONICAL_SCHEMA,
    RegistryKind.LOOKUPS: ArtifactKind.LOOKUP,
    RegistryKind.TRANSFORMS: ArtifactKind.TRANSFORM,
    RegistryKind.CROSSWALKS: ArtifactKind.CROSSWALK,
    RegistryKind.EVIDENCE_TEMPLATES: ArtifactKind.EVIDENCE_TEMPLATE,
    RegistryKind.PRECEDENCE_POLICIES: ArtifactKind.PRECEDENCE_POLICY,
    RegistryKind.READINESS_POLICIES: ArtifactKind.READINESS_POLICY,
    RegistryKind.AI_DEPLOYMENTS: ArtifactKind.AI_DEPLOYMENT,
    RegistryKind.AI_RUNTIME_PROFILES: ArtifactKind.AI_RUNTIME_PROFILE,
}

Reader = Annotated[Principal, Depends(require_permission(Permission.REGISTRY_READ))]
Manager = Annotated[Principal, Depends(require_permission(Permission.REGISTRY_MANAGE))]
Publisher = Annotated[Principal, Depends(require_permission(Permission.REGISTRY_PUBLISH))]
VersionPath = Annotated[str, Path(pattern=r"^\d{1,4}\.\d{1,4}$", examples=["1.0"])]

ERRORS = {
    404: {"model": ErrorResponse, "description": "Artifact or version not found in your tenant"},
    409: {"model": ErrorResponse,
          "description": "key_taken, version_conflict, not_editable, invalid_transition or draft_exists"},
    422: {"model": ErrorResponse, "description": "validation_failed with field errors"},
}


def _version(text: str) -> Version:
    try:
        return Version.parse(text)
    except ValueError as exc:
        raise service.ArtifactValidationError(str(exc), [FieldError("version", str(exc))]) from exc


def _summary(artifact: Artifact, v: ArtifactVersion, parents: dict[uuid.UUID, str]) -> ArtifactVersionSummary:
    ver = service.version_of(v)
    return ArtifactVersionSummary(
        id=v.id, version=str(ver), ref=format_ref(artifact.key, ver), status=v.status, digest=v.digest,
        parent_version=parents.get(v.parent_version_id) if v.parent_version_id else None,
        valid_from=v.valid_from, valid_to=v.valid_to,
        selectable=is_selectable(v.status, v.valid_from, v.valid_to, service.today()),
        change_note=v.change_note, status_reason=v.status_reason, created_by=v.created_by,
        created_at=v.created_at, updated_at=v.updated_at, published_by=v.published_by,
        published_at=v.published_at, lock_version=v.lock_version)


def _parents(db, artifact: Artifact) -> dict[uuid.UUID, str]:
    return {v.id: str(service.version_of(v)) for v in service.list_versions(db, artifact)}


def _version_out(db, artifact: Artifact, v: ArtifactVersion) -> ArtifactVersionOut:
    s = _summary(artifact, v, _parents(db, artifact))
    return ArtifactVersionOut(**s.model_dump(), artifact_id=artifact.id, kind=artifact.kind, key=artifact.key,
                              name=artifact.name, content=v.content,
                              digest_verified=service.verify_digest(artifact, v))


@router.get("/{kind}", response_model=ArtifactListOut, summary="List and search artifacts of a kind")
def list_artifacts(kind: RegistryKind, user: Reader, db: DbDep,
                   query: Annotated[str, Query(max_length=100, description="Key or name")] = "",
                   limit: Annotated[int, Query(ge=1, le=200)] = 50,
                   offset: Annotated[int, Query(ge=0)] = 0) -> ArtifactListOut:
    rows, total = service.list_artifacts(db, user.tenant_id, KINDS[kind], query=query, limit=limit, offset=offset)
    return ArtifactListOut(total=total, items=[
        ArtifactListItem(
            id=r.artifact.id, kind=r.artifact.kind, key=r.artifact.key, name=r.artifact.name,
            published_version=str(service.version_of(r.published)) if r.published else None,
            published_digest=r.published.digest if r.published else None,
            latest_version=str(service.version_of(r.latest)), latest_status=r.latest.status,
            updated_at=r.latest.updated_at)
        for r in rows
    ])


@router.post("/{kind}", response_model=ArtifactVersionOut, status_code=status.HTTP_201_CREATED,
             responses=ERRORS, summary="Create an artifact with draft version 1.0")
def create_artifact(kind: RegistryKind, body: ArtifactCreateIn, user: Manager, db: DbDep,
                    platform: PlatformDep) -> ArtifactVersionOut:
    artifact, v = service.create_artifact(db, platform.audit, user, KINDS[kind], **body.model_dump())
    return _version_out(db, artifact, v)


@router.get("/{kind}/content-schema", response_model=dict,
            summary="JSON schema of the content of this kind (structure only; meaning is checked on validate)")
def content_schema(kind: RegistryKind, _: Reader) -> dict:
    return json_schema_for(KINDS[kind])


@router.get("/{kind}/resolve", response_model=ArtifactRefOut, responses=ERRORS,
            summary="Resolve KEY (version new runs may select today) or KEY@1.0 (exact, for pinned runs)")
def resolve(kind: RegistryKind, user: Reader, db: DbDep,
            ref: Annotated[str, Query(max_length=80, examples=["TELCO-CANONICAL", "TELCO-CANONICAL@1.0"])]
            ) -> ArtifactRefOut:
    try:
        key, version = parse_ref(ref)
    except ValueError as exc:
        raise service.ArtifactValidationError(str(exc), [FieldError("ref", str(exc))]) from exc
    artifact, v = service.resolve(db, user.tenant_id, key, version, kind=KINDS[kind])
    ver = service.version_of(v)
    return ArtifactRefOut(artifact_id=artifact.id, version_id=v.id, kind=artifact.kind, key=artifact.key,
                          version=str(ver), ref=format_ref(artifact.key, ver), digest=v.digest, status=v.status,
                          selectable=is_selectable(v.status, v.valid_from, v.valid_to, service.today()))


@router.get("/{kind}/{artifact_id}", response_model=ArtifactOut, responses=ERRORS,
            summary="Artifact with its version history (newest first)")
def get_artifact(kind: RegistryKind, artifact_id: uuid.UUID, user: Reader, db: DbDep) -> ArtifactOut:
    artifact = service.get_artifact(db, user.tenant_id, KINDS[kind], artifact_id)
    versions = service.list_versions(db, artifact)
    parents = {v.id: str(service.version_of(v)) for v in versions}
    published = next((v for v in versions if v.status == ArtifactStatus.PUBLISHED), None)
    return ArtifactOut(
        id=artifact.id, kind=artifact.kind, key=artifact.key, name=artifact.name,
        description=artifact.description, created_by=artifact.created_by, created_at=artifact.created_at,
        published_version=str(service.version_of(published)) if published else None,
        versions=[_summary(artifact, v, parents) for v in versions])


@router.get("/{kind}/{artifact_id}/versions/{version}", response_model=ArtifactVersionOut, responses=ERRORS,
            summary="One version with its content")
def get_version(kind: RegistryKind, artifact_id: uuid.UUID, version: VersionPath, user: Reader,
                db: DbDep) -> ArtifactVersionOut:
    artifact = service.get_artifact(db, user.tenant_id, KINDS[kind], artifact_id)
    return _version_out(db, artifact, service.get_version(db, artifact, _version(version)))


@router.patch("/{kind}/{artifact_id}/versions/{version}", response_model=ArtifactVersionOut, responses=ERRORS,
              summary="Save a draft (content may still be invalid; send lock_version)")
def update_draft(kind: RegistryKind, artifact_id: uuid.UUID, version: VersionPath, body: ArtifactDraftPatch,
                 user: Manager, db: DbDep, platform: PlatformDep) -> ArtifactVersionOut:
    changes = body.model_dump(exclude_unset=True)
    lock = changes.pop("lock_version")
    artifact, v = service.update_draft(db, platform.audit, user, KINDS[kind], artifact_id, _version(version),
                                       changes, lock)
    return _version_out(db, artifact, v)


@router.delete("/{kind}/{artifact_id}/versions/{version}", status_code=status.HTTP_204_NO_CONTENT,
               responses=ERRORS, summary="Discard a draft (the artifact too, if it has no other version)")
def discard_draft(kind: RegistryKind, artifact_id: uuid.UUID, version: VersionPath,
                  lock_version: Annotated[int, Query()], user: Manager, db: DbDep, platform: PlatformDep) -> None:
    service.discard_draft(db, platform.audit, user, KINDS[kind], artifact_id, _version(version), lock_version)


@router.post("/{kind}/{artifact_id}/versions/{version}/validate", response_model=ArtifactValidationOut,
             responses=ERRORS, summary="Check a version without changing it (what publish would check)")
def validate(kind: RegistryKind, artifact_id: uuid.UUID, version: VersionPath, user: Reader,
             db: DbDep) -> ArtifactValidationOut:
    artifact = service.get_artifact(db, user.tenant_id, KINDS[kind], artifact_id)
    v = service.get_version(db, artifact, _version(version))
    errors = service.validate_version(db, artifact, v)
    return ArtifactValidationOut(valid=not errors, digest=v.digest,
                                 errors=[FieldErrorOut(field=e.field, message=e.message) for e in errors])


@router.post("/{kind}/{artifact_id}/versions/{version}/clone", response_model=ArtifactVersionOut,
             status_code=status.HTTP_201_CREATED, responses=ERRORS,
             summary="New draft copied from this version, numbered after the highest version")
def clone(kind: RegistryKind, artifact_id: uuid.UUID, version: VersionPath, body: ArtifactCloneIn,
          user: Manager, db: DbDep, platform: PlatformDep) -> ArtifactVersionOut:
    artifact, v = service.clone_version(db, platform.audit, user, KINDS[kind], artifact_id, _version(version),
                                        Bump(body.bump), body.change_note)
    return _version_out(db, artifact, v)


@router.post("/{kind}/{artifact_id}/versions/{version}/publish", response_model=ArtifactVersionOut,
             responses=ERRORS, summary="Validate and publish a draft; the previous published version is superseded")
def publish(kind: RegistryKind, artifact_id: uuid.UUID, version: VersionPath, body: ArtifactLockIn,
            user: Publisher, db: DbDep, platform: PlatformDep) -> ArtifactVersionOut:
    artifact, v = service.publish(db, platform.audit, user, KINDS[kind], artifact_id, _version(version),
                                  body.lock_version)
    return _version_out(db, artifact, v)


@router.post("/{kind}/{artifact_id}/versions/{version}/deprecate", response_model=ArtifactVersionOut,
             responses=ERRORS, summary="Stop new runs selecting a published version (pinned runs keep it)")
def deprecate(kind: RegistryKind, artifact_id: uuid.UUID, version: VersionPath, body: ArtifactStatusIn,
              user: Publisher, db: DbDep, platform: PlatformDep) -> ArtifactVersionOut:
    artifact, v = service.change_status(db, platform.audit, user, KINDS[kind], artifact_id, _version(version),
                                        ArtifactStatus.DEPRECATED, body.lock_version, body.reason)
    return _version_out(db, artifact, v)


@router.post("/{kind}/{artifact_id}/versions/{version}/retire", response_model=ArtifactVersionOut,
             responses=ERRORS, summary="Retire a version for good")
def retire(kind: RegistryKind, artifact_id: uuid.UUID, version: VersionPath, body: ArtifactStatusIn,
           user: Publisher, db: DbDep, platform: PlatformDep) -> ArtifactVersionOut:
    artifact, v = service.change_status(db, platform.audit, user, KINDS[kind], artifact_id, _version(version),
                                        ArtifactStatus.RETIRED, body.lock_version, body.reason)
    return _version_out(db, artifact, v)
