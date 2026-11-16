"""Mapping Rules API (step 5): suggestions, drafts, validation, review, publish, apply, staged output + lineage."""

import uuid
from datetime import datetime
from typing import Annotated, Any, Literal

from fastapi import APIRouter, Depends, Header, Query, Response, status
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.api.deps import CurrentUser, DbDep, PlatformDep, require_permission
from app.api.schemas import ErrorResponse
from app.db.models import MappingDefinition, MappingExecution, MappingVersion, SourceSchemaVersion
from app.domain.artifacts import ArtifactKind
from app.domain.mapping import MappingType, NullHandling, ProposedBy, Rule
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import mapping as service

router = APIRouter(tags=["mappings"])

Mapper = Annotated[Principal, Depends(require_permission(Permission.EVIDENCE_MAP))]
Approver = Annotated[Principal, Depends(require_permission(Permission.MAPPING_APPROVE))]
Publisher = Annotated[Principal, Depends(require_permission(Permission.MAPPING_PUBLISH))]

ERRORS = {
    403: {"model": ErrorResponse, "description": "Missing permission, or separation_of_duties (own submission)"},
    404: {"model": ErrorResponse, "description": "Run, mapping, version, upload or execution not found"},
    409: {"model": ErrorResponse, "description": "conflict, not_editable, version_conflict or invalid_transition"},
    422: {"model": ErrorResponse, "description": "validation_failed with field errors"},
}


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------

class RuleIO(BaseModel):
    key: str = Field(max_length=16, examples=["R1"])
    mapping_type: MappingType
    source_column: str | None = Field(default=None, max_length=255)
    target_entity: str | None = Field(default=None, max_length=64)
    target_field: str | None = Field(default=None, max_length=64)
    lookup_ref: str | None = Field(default=None, max_length=80, examples=["LIFECYCLE-STATUS@1.0"])
    transform_ref: str | None = Field(default=None, max_length=80)
    crosswalk_ref: str | None = Field(default=None, max_length=80)
    source_system: str | None = Field(default=None, max_length=64)
    derived: dict | None = None
    relationship: dict | None = None
    required: bool = False
    null_handling: NullHandling = NullHandling.KEEP_NULL
    default_value: str | None = Field(default=None, max_length=255)
    confidence: int | None = Field(default=None, ge=0, le=100, description="Suggestion confidence, not a score")
    proposed_by: ProposedBy = ProposedBy.USER


class SchemaRef(BaseModel):
    id: uuid.UUID
    version: int
    fingerprint: str
    sheet_name: str
    header_row: int


class ValidationOut(BaseModel):
    id: uuid.UUID
    status: str = Field(description="RUNNING | PASS | WARNING | BLOCKED | FAILED")
    content_hash: str
    matches_current_rules: bool
    result: dict | None
    created_at: datetime
    finished_at: datetime | None


class ApprovalOut(BaseModel):
    decision: str
    comment: str | None
    content_hash: str
    actor_name: str
    created_at: datetime


class ExecutionOut(BaseModel):
    id: uuid.UUID
    upload_id: uuid.UUID
    mapping_version_id: uuid.UUID
    status: str = Field(description="QUEUED | RUNNING | SUCCEEDED | FAILED")
    execution_key: str
    counts: dict | None
    rejected_rows: list | None
    conflicts: list | None
    output_hash: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None
    existing: bool = False


class MappingVersionSummary(BaseModel):
    version: int
    status: str
    content_hash: str | None
    created_at: datetime
    published_at: datetime | None


class MappingVersionOut(BaseModel):
    version_id: uuid.UUID
    mapping_id: uuid.UUID
    run_id: uuid.UUID
    domain: str
    version: int
    parent_version: int | None
    status: str
    status_reason: str | None
    lock_version: int
    content_hash: str | None = Field(description="Frozen hash once submitted")
    current_hash: str = Field(description="Hash of the rules as they are now")
    canonical_ref: str
    canonical_digest: str
    source_schema: SchemaRef
    columns: list[dict]
    rules: list[RuleIO]
    validation: ValidationOut | None
    approvals: list[ApprovalOut]
    executions: list[ExecutionOut]
    warnings_acknowledged: bool
    submitted_by: str | None
    submitted_at: datetime | None
    approved_by: str | None
    approved_at: datetime | None
    published_at: datetime | None
    targets: dict
    options: dict
    versions: list[MappingVersionSummary]


class CreateIn(BaseModel):
    schema_version_id: uuid.UUID | None = None


class DraftIn(BaseModel):
    lock_version: int
    rules: list[RuleIO] = Field(max_length=300)


class LockIn(BaseModel):
    lock_version: int


class SubmitIn(BaseModel):
    lock_version: int
    acknowledge_warnings: bool = False


class ReviewIn(BaseModel):
    decision: Literal["APPROVE", "RETURN", "REJECT"]
    content_hash: str
    comment: str | None = Field(default=None, max_length=2000)


class CloneIn(BaseModel):
    schema_version_id: uuid.UUID | None = None


class ApplyIn(BaseModel):
    mapping_version_id: uuid.UUID


class AISuggestOut(BaseModel):
    added: int
    source: str = Field(description="llm, fallback (no AI / AI failed) or none")
    mapping: MappingVersionOut


# ---------------------------------------------------------------------------
# Builders
# ---------------------------------------------------------------------------

def _rule_out(r: Rule) -> RuleIO:
    return RuleIO(**r.to_dict())


def _execution_out(ex: MappingExecution, existing: bool = False) -> ExecutionOut:
    return ExecutionOut(id=ex.id, upload_id=ex.upload_id, mapping_version_id=ex.mapping_version_id, status=ex.status,
                        execution_key=ex.execution_key, counts=ex.counts, rejected_rows=ex.rejected_rows,
                        conflicts=ex.conflicts, output_hash=ex.output_hash, error=ex.error,
                        created_at=ex.created_at, finished_at=ex.finished_at, existing=existing)


def _targets(canonical: dict) -> dict:
    return {
        "entities": [{"name": e["name"], "label": e.get("label", e["name"]), "keyFields": e["keyFields"],
                      "fields": [{k: f[k] for k in ("name", "label", "type", "required", "values", "references")
                                  if k in f} for f in e["fields"]]} for e in canonical["entities"]],
        "relationships": [{"name": r["name"], "from": r["from"] if isinstance(r["from"], list) else [r["from"]],
                           "to": r["to"]} for r in canonical.get("relationships", [])],
    }


def _options(db: Session, tenant_id: str) -> dict:
    def refs(kind: ArtifactKind, describe) -> list[dict]:
        return [{"key": k, "ref": ref, **describe(c)} for k, (ref, c) in
                sorted(service._selectable_refs(db, tenant_id, kind).items())]
    return {
        "lookups": refs(ArtifactKind.LOOKUP, lambda c: {"target": c["target"]}),
        "transforms": refs(ArtifactKind.TRANSFORM, lambda c: {"input": c["input"], "output": c["output"]}),
        "crosswalks": refs(ArtifactKind.CROSSWALK, lambda c: {"target": c["target"]}),
    }


def _version_out(db: Session, definition: MappingDefinition, v: MappingVersion) -> MappingVersionOut:
    rules = service.rules_of(db, v)
    schema: SourceSchemaVersion = service.schema_of(db, v)
    ctx = service.context_for(db, v, rules)
    current = service.current_hash(db, v, rules)
    vr = service.latest_validation(db, v)
    parent = db.get(MappingVersion, v.parent_version_id) if v.parent_version_id else None
    return MappingVersionOut(
        version_id=v.id, mapping_id=definition.id, run_id=definition.run_id, domain=definition.domain, version=v.version,
        parent_version=parent.version if parent else None, status=v.status, status_reason=v.status_reason,
        lock_version=v.lock_version, content_hash=v.content_hash, current_hash=current,
        canonical_ref=v.canonical_ref, canonical_digest=v.canonical_digest,
        source_schema=SchemaRef(id=schema.id, version=schema.version, fingerprint=schema.fingerprint,
                                sheet_name=schema.sheet_name, header_row=schema.header_row),
        columns=service._samples_for(db, schema), rules=[_rule_out(r) for r in rules],
        validation=ValidationOut(id=vr.id, status=vr.status, content_hash=vr.content_hash,
                                 matches_current_rules=vr.content_hash == current, result=vr.result,
                                 created_at=vr.created_at, finished_at=vr.finished_at) if vr else None,
        approvals=[ApprovalOut(decision=a.decision, comment=a.comment, content_hash=a.content_hash,
                               actor_name=a.actor_name, created_at=a.created_at) for a in service.approvals_of(db, v)],
        executions=[_execution_out(e) for e in service.executions_of(db, v)],
        warnings_acknowledged=v.warnings_acknowledged, submitted_by=v.submitted_by, submitted_at=v.submitted_at,
        approved_by=v.approved_by, approved_at=v.approved_at, published_at=v.published_at,
        targets=_targets(ctx.canonical), options=_options(db, definition.tenant_id),
        versions=[MappingVersionSummary(version=x.version, status=x.status, content_hash=x.content_hash,
                                        created_at=x.created_at, published_at=x.published_at)
                  for x in service.list_versions(db, definition)])


def _load(db: Session, user: Principal, run_id: uuid.UUID, mapping_id: uuid.UUID, version: int):
    d = service.get_definition(db, user.tenant_id, run_id, mapping_id)
    return d, service.get_version(db, d, version)


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------

@router.get("/runs/{run_id}/evidence/{domain}/mapping", response_model=MappingVersionOut, responses=ERRORS,
            summary="The mapping of a domain's source (newest version)")
def get_domain_mapping(run_id: uuid.UUID, domain: str, user: CurrentUser, db: DbDep) -> MappingVersionOut:
    d = service.definition_for_domain(db, user.tenant_id, run_id, domain)
    return _version_out(db, d, service.list_versions(db, d)[0])


@router.post("/runs/{run_id}/evidence/{domain}/mapping", response_model=MappingVersionOut,
             status_code=status.HTTP_201_CREATED, responses=ERRORS,
             summary="Create the mapping (draft v1 with deterministic suggestions)")
def create_mapping(run_id: uuid.UUID, domain: str, body: CreateIn, user: Mapper, db: DbDep,
                   platform: PlatformDep) -> MappingVersionOut:
    d, v = service.create_mapping(db, platform, user, run_id, domain, body.schema_version_id)
    return _version_out(db, d, v)


@router.get("/runs/{run_id}/mappings/{mapping_id}/versions/{version}", response_model=MappingVersionOut,
            responses=ERRORS, summary="One mapping version with rules, validation, approvals and applies")
def get_mapping_version(run_id: uuid.UUID, mapping_id: uuid.UUID, version: int, user: CurrentUser,
                        db: DbDep) -> MappingVersionOut:
    d, v = _load(db, user, run_id, mapping_id, version)
    return _version_out(db, d, v)


@router.patch("/runs/{run_id}/mappings/{mapping_id}/versions/{version}", response_model=MappingVersionOut,
              responses=ERRORS, summary="Save the rules of an open version (returns it to DRAFT)")
def save_draft(run_id: uuid.UUID, mapping_id: uuid.UUID, version: int, body: DraftIn, user: Mapper, db: DbDep,
               platform: PlatformDep) -> MappingVersionOut:
    service.save_draft(db, platform, user, run_id, mapping_id, version,
                       [r.model_dump(mode="json") for r in body.rules], body.lock_version)
    return _version_out(db, *_load(db, user, run_id, mapping_id, version))


@router.post("/runs/{run_id}/mappings/{mapping_id}/versions/{version}/validate", response_model=MappingVersionOut,
             status_code=status.HTTP_202_ACCEPTED, responses=ERRORS,
             summary="Validate against the confirmed file (background job)")
def validate_version(run_id: uuid.UUID, mapping_id: uuid.UUID, version: int, user: Mapper, db: DbDep,
                     platform: PlatformDep) -> MappingVersionOut:
    service.request_validation(db, platform, user, run_id, mapping_id, version)
    return _version_out(db, *_load(db, user, run_id, mapping_id, version))


@router.post("/runs/{run_id}/mappings/{mapping_id}/versions/{version}/ai-suggestions", response_model=AISuggestOut,
             responses=ERRORS, summary="Ask AI to suggest targets for unmapped columns (headers + 5 sample rows only)")
def ai_suggestions(run_id: uuid.UUID, mapping_id: uuid.UUID, version: int, body: LockIn, user: Mapper, db: DbDep,
                   platform: PlatformDep) -> AISuggestOut:
    out = service.ai_suggest(db, platform, user, run_id, mapping_id, version, body.lock_version)
    return AISuggestOut(added=out["added"], source=out["source"],
                        mapping=_version_out(db, *_load(db, user, run_id, mapping_id, version)))


@router.post("/runs/{run_id}/mappings/{mapping_id}/versions/{version}/submit", response_model=MappingVersionOut,
             responses=ERRORS, summary="Freeze the version (content hash) and send it for review")
def submit(run_id: uuid.UUID, mapping_id: uuid.UUID, version: int, body: SubmitIn, user: Mapper, db: DbDep,
           platform: PlatformDep) -> MappingVersionOut:
    service.submit(db, platform, user, run_id, mapping_id, version, body.lock_version, body.acknowledge_warnings)
    return _version_out(db, *_load(db, user, run_id, mapping_id, version))


@router.post("/runs/{run_id}/mappings/{mapping_id}/versions/{version}/review", response_model=MappingVersionOut,
             responses=ERRORS, summary="Approve, return or reject the exact submitted content hash")
def review(run_id: uuid.UUID, mapping_id: uuid.UUID, version: int, body: ReviewIn, user: Approver, db: DbDep,
           platform: PlatformDep) -> MappingVersionOut:
    service.review(db, platform, user, run_id, mapping_id, version, body.decision, body.content_hash, body.comment)
    return _version_out(db, *_load(db, user, run_id, mapping_id, version))


@router.post("/runs/{run_id}/mappings/{mapping_id}/versions/{version}/publish", response_model=MappingVersionOut,
             responses=ERRORS, summary="Publish an approved version (supersedes the previous published one)")
def publish(run_id: uuid.UUID, mapping_id: uuid.UUID, version: int, body: LockIn, user: Publisher, db: DbDep,
            platform: PlatformDep) -> MappingVersionOut:
    service.publish(db, platform, user, run_id, mapping_id, version, body.lock_version)
    return _version_out(db, *_load(db, user, run_id, mapping_id, version))


@router.post("/runs/{run_id}/mappings/{mapping_id}/versions/{version}/clone", response_model=MappingVersionOut,
             status_code=status.HTTP_201_CREATED, responses=ERRORS,
             summary="New draft from this version (optionally for a newer source schema)")
def clone(run_id: uuid.UUID, mapping_id: uuid.UUID, version: int, body: CloneIn, user: Mapper, db: DbDep,
          platform: PlatformDep) -> MappingVersionOut:
    new = service.clone_version(db, platform, user, run_id, mapping_id, version, body.schema_version_id)
    d = service.get_definition(db, user.tenant_id, run_id, mapping_id)
    return _version_out(db, d, new)


@router.get("/runs/{run_id}/mappings/{mapping_id}/versions/{version}/compatibility", response_model=dict,
            responses=ERRORS, summary="Does a source schema version have every column this version uses?")
def compatibility(run_id: uuid.UUID, mapping_id: uuid.UUID, version: int, user: CurrentUser, db: DbDep,
                  schema_version_id: Annotated[uuid.UUID, Query()]) -> dict:
    d, v = _load(db, user, run_id, mapping_id, version)
    schema = db.get(SourceSchemaVersion, schema_version_id)
    if schema is None or schema.source_id != d.source_id or schema.tenant_id != user.tenant_id:
        raise service.MappingNotFound("Source schema version not found for this source")
    return service.compatibility(db, v, schema)


@router.post("/runs/{run_id}/evidence/uploads/{upload_id}/apply", response_model=ExecutionOut,
             status_code=status.HTTP_202_ACCEPTED,
             responses={200: {"model": ExecutionOut, "description": "Already applied or running (existing=true)"},
                        **ERRORS},
             summary="Apply a published mapping version to a confirmed upload (background job, idempotent)")
def apply(run_id: uuid.UUID, upload_id: uuid.UUID, body: ApplyIn, response: Response, user: Mapper, db: DbDep,
          platform: PlatformDep,
          idempotency_key: Annotated[str | None, Header(alias="Idempotency-Key", max_length=128)] = None
          ) -> ExecutionOut:
    out = service.request_apply(db, platform, user, run_id, upload_id, body.mapping_version_id, idempotency_key)
    if out.existing:
        response.status_code = status.HTTP_200_OK
    return _execution_out(out.execution, out.existing)


@router.get("/runs/{run_id}/mapping-executions/{execution_id}", response_model=ExecutionOut, responses=ERRORS,
            summary="Status and counts of one apply")
def get_execution(run_id: uuid.UUID, execution_id: uuid.UUID, user: CurrentUser, db: DbDep) -> ExecutionOut:
    return _execution_out(service.get_execution(db, user.tenant_id, run_id, execution_id))


@router.get("/runs/{run_id}/mapping-executions/{execution_id}/records", response_model=dict, responses=ERRORS,
            summary="Staged canonical records of an apply, with field lineage")
def staged_records(run_id: uuid.UUID, execution_id: uuid.UUID, user: CurrentUser, db: DbDep,
                   entity: Annotated[str | None, Query(max_length=64)] = None,
                   limit: Annotated[int, Query(ge=1, le=200)] = 50,
                   offset: Annotated[int, Query(ge=0)] = 0) -> dict[str, Any]:
    ex = service.get_execution(db, user.tenant_id, run_id, execution_id)
    total, records, lineage = service.staged_records(db, ex, entity, limit, offset)
    return {"total": total, "items": [{
        "id": str(r.id), "entity": r.entity, "key": r.record_key, "values": r.values, "source_rows": r.source_rows,
        "lineage": [{"field": lin.canonical_field, "canonical_value": lin.canonical_value,
                     "source_system": lin.source_system, "source_key": lin.source_key,
                     "source_field": lin.source_field, "source_value": lin.source_value,
                     "upload_id": str(lin.upload_id), "sheet": lin.sheet, "row": lin.source_row,
                     "rule": lin.mapping_rule_key, "mapping_type": lin.mapping_type, "lookup_ref": lin.lookup_ref,
                     "transform_ref": lin.transform_ref, "crosswalk_ref": lin.crosswalk_ref,
                     "mapping_version_id": str(lin.mapping_version_id)} for lin in lineage.get(r.id, [])],
    } for r in records]}

