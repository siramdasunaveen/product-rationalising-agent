"""Mapping Rules service (step 5): create with suggestions, edit drafts, validate (background job), submit,
review (separation of duties), publish, clone, compatibility, optional AI suggestions, apply (background job)
with staged records and field lineage.

Deterministic throughout; AI only proposes column -> field suggestions for unmapped columns and never
approves, validates or applies anything.
"""

import hashlib
import json
import uuid
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.registry import Platform
from app.adapters.sources.excel import ExcelSourceAdapter
from app.db.models import (
    Artifact,
    EvidenceSource,
    EvidenceUpload,
    FieldLineage,
    MappingApproval,
    MappingDefinition,
    MappingExecution,
    MappingRuleRow,
    MappingValidationRun,
    MappingVersion,
    Run,
    SourceSchemaVersion,
    StagedRecord,
    StagedRelationship,
)
from app.domain.artifacts import ArtifactKind, format_ref, is_selectable, parse_ref
from app.domain.common import FieldError
from app.domain.evidence import DOMAINS, EvidenceDomain, SourceType, UploadStatus
from app.domain.excel_checks import FileLimits
from app.domain.mapping import (
    EDITABLE,
    TRANSITIONS,
    Context,
    MappingStatus,
    MappingType,
    NullHandling,
    ProposedBy,
    RefInfo,
    Rule,
    content_hash,
    execute,
    output_hash,
    rule_diff,
    validate,
)
from app.domain.mapping_suggest import choose_rule, suggest
from app.ports.ai import AIRequest
from app.ports.audit import AuditLog, AuditRecord
from app.ports.auth import Principal
from app.ports.notify import Notification
from app.services import artifacts as registry
from app.services.ai_runner import run_ai_task
from app.services.errors import ServiceError
from app.services.evidence import EDITABLE_STATES, selected_domains, template_for
from app.services.outbox import emit

M = MappingStatus
VALIDATE_JOB = "app.jobs.mapping.validate_mapping"
EXECUTE_JOB = "app.jobs.mapping.execute_mapping"
COMMENT_MIN, COMMENT_MAX = 3, 2000
MAX_RULES = 300
DEFAULT_CANONICAL = "TELCO-CANONICAL"
APPROVER_ROLES = ("data_steward", "agent_admin")


class MappingError(ServiceError):
    code = "mapping_error"


class MappingValidationError(MappingError):
    status_code = 422
    code = "validation_failed"


class MappingNotFound(MappingError):
    status_code = 404
    code = "not_found"


class MappingConflict(MappingError):
    status_code = 409
    code = "conflict"


class MappingNotEditable(MappingError):
    status_code = 409
    code = "not_editable"


class MappingVersionConflict(MappingError):
    status_code = 409
    code = "version_conflict"


class MappingInvalidTransition(MappingError):
    status_code = 409
    code = "invalid_transition"


class SeparationOfDuties(MappingError):
    status_code = 403
    code = "separation_of_duties"


def _now() -> datetime:
    return datetime.now(UTC)


# ---------------------------------------------------------------------------
# Loading helpers
# ---------------------------------------------------------------------------

def _run(session: Session, tenant_id: str, run_id: uuid.UUID) -> Run:
    run = session.scalar(select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id))
    if run is None:
        raise MappingNotFound("Run not found")
    return run


def _require_run_editable(run: Run) -> None:
    if run.state not in EDITABLE_STATES:
        raise MappingNotEditable(f"Mappings can only be changed while the run is configuring (now {run.state})")


def _excel_source(session: Session, run: Run, domain: str) -> EvidenceSource:
    try:
        d = EvidenceDomain(domain)
    except ValueError as exc:
        raise MappingNotFound(f"Unknown evidence domain {domain}") from exc
    src = session.scalar(select(EvidenceSource).where(
        EvidenceSource.run_id == run.id, EvidenceSource.domain == d.value,
        EvidenceSource.source_type == SourceType.EXCEL_UPLOAD.value))
    if src is None:
        raise MappingConflict("Upload a file and confirm its sheet before mapping",
                              [FieldError("domain", "No confirmed source for this domain yet")])
    return src


def get_definition(session: Session, tenant_id: str, run_id: uuid.UUID, mapping_id: uuid.UUID) -> MappingDefinition:
    d = session.scalar(select(MappingDefinition).where(MappingDefinition.id == mapping_id,
                                                       MappingDefinition.run_id == run_id,
                                                       MappingDefinition.tenant_id == tenant_id))
    if d is None:
        raise MappingNotFound("Mapping not found")
    return d


def definition_for_domain(session: Session, tenant_id: str, run_id: uuid.UUID, domain: str) -> MappingDefinition:
    d = session.scalar(select(MappingDefinition).where(MappingDefinition.run_id == run_id,
                                                       MappingDefinition.domain == domain,
                                                       MappingDefinition.tenant_id == tenant_id))
    if d is None:
        raise MappingNotFound("No mapping for this domain yet")
    return d


def list_versions(session: Session, definition: MappingDefinition) -> list[MappingVersion]:
    return list(session.scalars(select(MappingVersion).where(MappingVersion.definition_id == definition.id)
                                .order_by(MappingVersion.version.desc())))


def get_version(session: Session, definition: MappingDefinition, number: int, *,
                for_update: bool = False) -> MappingVersion:
    stmt = select(MappingVersion).where(MappingVersion.definition_id == definition.id,
                                        MappingVersion.version == number)
    if for_update:
        stmt = stmt.with_for_update()
    v = session.scalar(stmt)
    if v is None:
        raise MappingNotFound(f"Version {number} not found")
    return v


def rules_of(session: Session, version: MappingVersion) -> list[Rule]:
    rows = session.scalars(select(MappingRuleRow).where(MappingRuleRow.version_id == version.id)
                           .order_by(MappingRuleRow.position))
    return [_rule_from_row(r) for r in rows]


def _rule_from_row(r: MappingRuleRow) -> Rule:
    p = r.params or {}
    return Rule(key=r.rule_key, mapping_type=MappingType(r.mapping_type), source_column=r.source_column,
                target_entity=r.target_entity, target_field=r.target_field, lookup_ref=p.get("lookup_ref"),
                transform_ref=p.get("transform_ref"), crosswalk_ref=p.get("crosswalk_ref"),
                source_system=p.get("source_system"), derived=p.get("derived"), relationship=p.get("relationship"),
                required=r.required, null_handling=NullHandling(r.null_handling), default_value=r.default_value,
                confidence=r.suggestion_confidence, proposed_by=ProposedBy(r.proposed_by))


def _rule_row(tenant_id: str, version_id: uuid.UUID, position: int, r: Rule) -> MappingRuleRow:
    params = {k: v for k, v in {"lookup_ref": r.lookup_ref, "transform_ref": r.transform_ref,
                                "crosswalk_ref": r.crosswalk_ref, "source_system": r.source_system,
                                "derived": r.derived, "relationship": r.relationship}.items() if v is not None}
    return MappingRuleRow(tenant_id=tenant_id, version_id=version_id, rule_key=r.key, position=position,
                          mapping_type=r.mapping_type.value, source_column=r.source_column,
                          target_entity=r.target_entity, target_field=r.target_field, params=params,
                          required=r.required, null_handling=r.null_handling.value, default_value=r.default_value,
                          suggestion_confidence=r.confidence, proposed_by=r.proposed_by.value)


def _replace_rules(session: Session, version: MappingVersion, rules: list[Rule]) -> None:
    for row in session.scalars(select(MappingRuleRow).where(MappingRuleRow.version_id == version.id)):
        session.delete(row)
    session.flush()
    for i, r in enumerate(rules):
        session.add(_rule_row(version.tenant_id, version.id, i, r))
    session.flush()


def schema_of(session: Session, version: MappingVersion) -> SourceSchemaVersion:
    return session.get(SourceSchemaVersion, version.source_schema_version_id)


def columns_of(schema: SourceSchemaVersion) -> dict[str, str]:
    return {c["header"]: c["type"] for c in schema.columns}


def _samples_for(session: Session, schema: SourceSchemaVersion) -> list[dict[str, Any]]:
    """Columns with up to 5 sample values, from the same file validation uses (the newest confirmed upload with
    this structure), so what the grid shows matches what is validated."""
    upload = session.scalar(select(EvidenceUpload).where(
        EvidenceUpload.schema_version_id == schema.id, EvidenceUpload.status == UploadStatus.READY.value)
        .order_by(EvidenceUpload.confirmed_at.desc()).limit(1)) or session.get(EvidenceUpload, schema.first_upload_id)
    sheet = next((s for s in (upload.discovery or {}).get("sheets", []) if s["name"] == schema.sheet_name), None) \
        if upload else None
    by_header: dict[str, list] = {}
    if sheet and sheet.get("header_row") == schema.header_row:
        for j, c in enumerate(sheet["columns"]):
            by_header[c["header"]] = [row[j] for row in sheet["samples"] if j < len(row)]
    return [{"header": c["header"], "type": c["type"], "samples": by_header.get(c["header"], [])}
            for c in schema.columns]


def _selectable_refs(session: Session, tenant_id: str, kind: ArtifactKind) -> dict[str, tuple[str, dict]]:
    out: dict[str, tuple[str, dict]] = {}
    for a in session.scalars(select(Artifact).where(Artifact.tenant_id == tenant_id, Artifact.kind == kind.value)):
        v = registry.selectable_version(session, a)
        if v is not None:
            out[a.key] = (format_ref(a.key, registry.version_of(v)), v.content)
    return out


def _resolve_ref(session: Session, tenant_id: str, ref: str) -> RefInfo | None:
    try:
        key, ver = parse_ref(ref)
        artifact, v = registry.resolve(session, tenant_id, key, ver)
    except (ValueError, registry.ArtifactNotFound):
        return None
    return RefInfo(kind=artifact.kind, status=v.status,
                   selectable=is_selectable(v.status, v.valid_from, v.valid_to, registry.today()), content=v.content)


def context_for(session: Session, version: MappingVersion, rules: list[Rule]) -> Context:
    key, ver = parse_ref(version.canonical_ref)
    _, canonical = registry.resolve(session, version.tenant_id, key, ver, kind=ArtifactKind.CANONICAL_SCHEMA)
    refs = {ref: _resolve_ref(session, version.tenant_id, ref) for r in rules for ref in r.refs}
    return Context(canonical=canonical.content, refs=refs, columns=columns_of(schema_of(session, version)))


def current_hash(session: Session, version: MappingVersion, rules: list[Rule] | None = None) -> str:
    rules = rules if rules is not None else rules_of(session, version)
    return content_hash(schema_of(session, version).fingerprint, version.canonical_ref, version.canonical_digest,
                        rules)


def _audit(audit: AuditLog, session: Session, principal: Principal, action: str, version: MappingVersion,
           definition: MappingDefinition, extra: dict[str, Any] | None = None) -> dict[str, Any]:
    payload = {"mapping_id": str(definition.id), "run_id": str(definition.run_id), "domain": definition.domain,
               "version": version.version, "status": version.status, "content_hash": version.content_hash,
               **(extra or {})}
    audit.record(AuditRecord(tenant_id=principal.tenant_id, actor_id=principal.user_id, actor_roles=principal.roles,
                             auth_context=principal.auth_context, action=action, entity_type="mapping_version",
                             entity_id=str(version.id), payload=payload), session=session)
    return payload


def _event(session: Session, tenant_id: str, event_type: str, definition: MappingDefinition,
           payload: dict[str, Any]) -> None:
    emit(session, tenant_id=tenant_id, event_type=event_type, aggregate_type="mapping",
         aggregate_id=str(definition.id), payload=payload)


def _check_lock(v: MappingVersion, expected: int) -> None:
    if v.lock_version != expected:
        raise MappingVersionConflict("This mapping version was changed by someone else. Reload and try again.")


def _set_status(v: MappingVersion, target: MappingStatus, reason: str | None = None) -> None:
    if v.status != target.value and target not in TRANSITIONS[MappingStatus(v.status)]:
        raise MappingInvalidTransition(f"Version {v.version} is {v.status} and cannot become {target}")
    v.status = target.value
    if reason is not None:
        v.status_reason = reason
    v.lock_version += 1
    v.updated_at = _now()


def _upload_for_schema(session: Session, schema: SourceSchemaVersion) -> EvidenceUpload:
    upload = session.scalar(select(EvidenceUpload).where(
        EvidenceUpload.schema_version_id == schema.id, EvidenceUpload.status == UploadStatus.READY.value)
        .order_by(EvidenceUpload.confirmed_at.desc()).limit(1))
    if upload is None or not upload.storage_key:
        raise MappingConflict("No checked file with this structure is available to validate against")
    return upload


# ---------------------------------------------------------------------------
# Create (with deterministic suggestions)
# ---------------------------------------------------------------------------

def _prior_rules(session: Session, tenant_id: str, domain: str, fingerprint: str) -> list[Rule] | None:
    v = session.scalar(
        select(MappingVersion)
        .join(MappingDefinition, MappingDefinition.id == MappingVersion.definition_id)
        .join(SourceSchemaVersion, SourceSchemaVersion.id == MappingVersion.source_schema_version_id)
        .where(MappingVersion.tenant_id == tenant_id, MappingDefinition.domain == domain,
               MappingVersion.status == M.PUBLISHED.value, SourceSchemaVersion.fingerprint == fingerprint)
        .order_by(MappingVersion.published_at.desc()).limit(1))
    return rules_of(session, v) if v else None


def create_mapping(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID, domain: str,
                   schema_version_id: uuid.UUID | None = None) -> tuple[MappingDefinition, MappingVersion]:
    run = _run(session, principal.tenant_id, run_id)
    _require_run_editable(run)
    source = _excel_source(session, run, domain)
    if EvidenceDomain(domain) not in selected_domains(run):
        raise MappingConflict("This domain is not selected for the run")
    existing = session.scalar(select(MappingDefinition).where(MappingDefinition.source_id == source.id))
    if existing is not None:
        raise MappingConflict("A mapping already exists for this source; clone a version to change it")
    stmt = select(SourceSchemaVersion).where(SourceSchemaVersion.source_id == source.id)
    stmt = stmt.where(SourceSchemaVersion.id == schema_version_id) if schema_version_id else \
        stmt.order_by(SourceSchemaVersion.version.desc()).limit(1)
    schema = session.scalar(stmt)
    if schema is None:
        raise MappingConflict("Confirm a sheet and header row first",
                              [FieldError("schema_version_id", "No confirmed source schema")])

    tpl = template_for(session, principal.tenant_id, EvidenceDomain(domain))
    canonical_key = tpl[1]["canonicalSchema"] if tpl else DEFAULT_CANONICAL
    try:
        c_art, c_ver = registry.resolve(session, principal.tenant_id, canonical_key, kind=ArtifactKind.CANONICAL_SCHEMA)
    except registry.ArtifactNotFound as exc:
        raise MappingConflict(f"No published canonical schema {canonical_key}") from exc
    entities = list(DOMAINS[EvidenceDomain(domain)].canonical_entities)
    for col in (tpl[1]["columns"] if tpl else []):
        if col.get("target") and col["target"]["entity"] not in entities:
            entities.append(col["target"]["entity"])
    rules = suggest(_samples_for(session, schema), canonical=c_ver.content, entities=entities,
                    template=tpl[1] if tpl else None,
                    lookups=_selectable_refs(session, principal.tenant_id, ArtifactKind.LOOKUP),
                    transforms=_selectable_refs(session, principal.tenant_id, ArtifactKind.TRANSFORM),
                    prior=_prior_rules(session, principal.tenant_id, domain, schema.fingerprint))

    definition = MappingDefinition(tenant_id=principal.tenant_id, run_id=run.id, source_id=source.id, domain=domain,
                                   created_by=principal.user_id)
    session.add(definition)
    try:
        session.flush()
    except IntegrityError as exc:
        session.rollback()
        raise MappingConflict("A mapping already exists for this source") from exc
    version = MappingVersion(tenant_id=principal.tenant_id, definition_id=definition.id, version=1,
                             source_schema_version_id=schema.id,
                             canonical_ref=format_ref(c_art.key, registry.version_of(c_ver)),
                             canonical_digest=c_ver.digest, status=M.DRAFT.value, warnings_acknowledged=False,
                             lock_version=1, created_by=principal.user_id)
    session.add(version)
    session.flush()
    _replace_rules(session, version, rules)
    payload = _audit(platform.audit, session, principal, "mapping.created", version, definition, {
        "suggested_rules": len(rules), "suggested_by": sorted({r.proposed_by.value for r in rules}),
        "template_ref": tpl[0] if tpl else None, "source_schema_version": schema.version})
    _event(session, principal.tenant_id, "MappingDraftCreated", definition, payload)
    session.commit()
    return definition, version


# ---------------------------------------------------------------------------
# Draft editing, clone, compatibility
# ---------------------------------------------------------------------------

def _parse_rules(items: list[dict[str, Any]]) -> list[Rule]:
    if len(items) > MAX_RULES:
        raise MappingValidationError("Too many rules", [FieldError("rules", f"At most {MAX_RULES} rules")])
    rules = []
    errors: list[FieldError] = []
    for i, it in enumerate(items):
        try:
            r = Rule.from_dict(it)
        except (TypeError, ValueError, KeyError) as exc:
            errors.append(FieldError(f"rules[{i}]", f"Not a valid rule: {exc}"))
            continue
        if not r.key or len(r.key) > 16:
            errors.append(FieldError(f"rules[{i}].key", "Rule id must be 1-16 characters"))
        rules.append(r)
    if errors:
        raise MappingValidationError("Some rules are not valid", errors)
    return rules


def save_draft(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID, mapping_id: uuid.UUID,
               number: int, rules_input: list[dict[str, Any]], expected_lock: int) -> MappingVersion:
    run = _run(session, principal.tenant_id, run_id)
    _require_run_editable(run)
    definition = get_definition(session, principal.tenant_id, run_id, mapping_id)
    v = get_version(session, definition, number, for_update=True)
    if MappingStatus(v.status) not in EDITABLE:
        raise MappingNotEditable(f"Version {v.version} is {v.status}; clone it to make changes")
    _check_lock(v, expected_lock)
    rules = _parse_rules(rules_input)
    before = rules_of(session, v)
    if v.status != M.DRAFT.value:
        _set_status(v, M.DRAFT)
    else:
        v.lock_version += 1
        v.updated_at = _now()
    v.warnings_acknowledged = False
    session.flush()
    _replace_rules(session, v, rules)
    _audit(platform.audit, session, principal, "mapping.draft_saved", v, definition,
           {"rules": len(rules), "diff": rule_diff(before, rules)})
    session.commit()
    return v


def clone_version(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID,
                  mapping_id: uuid.UUID, number: int, schema_version_id: uuid.UUID | None = None) -> MappingVersion:
    run = _run(session, principal.tenant_id, run_id)
    _require_run_editable(run)
    definition = get_definition(session, principal.tenant_id, run_id, mapping_id)
    session.execute(select(MappingDefinition.id).where(MappingDefinition.id == definition.id).with_for_update())
    src = get_version(session, definition, number)
    versions = list_versions(session, definition)
    open_v = next((x for x in versions if MappingStatus(x.status) in EDITABLE or x.status == M.VALIDATING.value), None)
    if open_v is not None:
        raise MappingConflict(f"Version {open_v.version} is still open ({open_v.status}); finish or change it first")
    schema_id = src.source_schema_version_id
    if schema_version_id is not None:
        schema = session.get(SourceSchemaVersion, schema_version_id)
        if schema is None or schema.source_id != definition.source_id or schema.tenant_id != principal.tenant_id:
            raise MappingValidationError("Unknown source schema version",
                                         [FieldError("schema_version_id", "Not a schema of this source")])
        schema_id = schema.id
    new = MappingVersion(tenant_id=principal.tenant_id, definition_id=definition.id, version=versions[0].version + 1,
                         parent_version_id=src.id, source_schema_version_id=schema_id,
                         canonical_ref=src.canonical_ref, canonical_digest=src.canonical_digest, status=M.DRAFT.value,
                         warnings_acknowledged=False, lock_version=1, created_by=principal.user_id)
    session.add(new)
    session.flush()
    _replace_rules(session, new, rules_of(session, src))
    payload = _audit(platform.audit, session, principal, "mapping.version_created", new, definition,
                     {"parent_version": src.version})
    _event(session, principal.tenant_id, "MappingDraftCreated", definition, payload)
    session.commit()
    return new


def compatibility(session: Session, version: MappingVersion, schema: SourceSchemaVersion) -> dict[str, Any]:
    rules = rules_of(session, version)
    used = {c for r in rules for c in r.columns_used()}
    cols = set(columns_of(schema))
    missing = sorted(used - cols)
    return {"compatible": not missing, "missing_columns": missing, "new_columns": sorted(cols - used),
            "schema_version": schema.version, "fingerprint": schema.fingerprint}


# ---------------------------------------------------------------------------
# Validation (background job)
# ---------------------------------------------------------------------------

def request_validation(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID,
                       mapping_id: uuid.UUID, number: int) -> MappingValidationRun:
    definition = get_definition(session, principal.tenant_id, run_id, mapping_id)
    v = get_version(session, definition, number, for_update=True)
    if MappingStatus(v.status) not in EDITABLE:
        raise MappingNotEditable(f"Version {v.version} is {v.status}; only open versions are validated")
    upload = _upload_for_schema(session, schema_of(session, v))
    run_row = MappingValidationRun(tenant_id=principal.tenant_id, version_id=v.id, upload_id=upload.id,
                                   content_hash=current_hash(session, v), status="RUNNING",
                                   requested_by=principal.user_id)
    session.add(run_row)
    _set_status(v, M.VALIDATING)
    session.flush()
    _event(session, principal.tenant_id, "MappingValidationRequested", definition,
           {"version": v.version, "validation_id": str(run_row.id)})
    session.commit()
    run_row.job_id = platform.jobs.enqueue(VALIDATE_JOB, str(run_row.id))
    session.commit()
    return run_row


def run_validation(session: Session, platform: Platform, limits: FileLimits,
                   validation_id: uuid.UUID) -> MappingValidationRun:
    vr = session.scalar(select(MappingValidationRun).where(MappingValidationRun.id == validation_id).with_for_update())
    if vr is None:
        raise MappingNotFound("Validation not found")
    if vr.status != "RUNNING":
        session.commit()
        return vr
    v = session.scalar(select(MappingVersion).where(MappingVersion.id == vr.version_id).with_for_update())
    definition = session.get(MappingDefinition, v.definition_id)
    rules = rules_of(session, v)
    if v.status != M.VALIDATING.value or current_hash(session, v, rules) != vr.content_hash:
        vr.status, vr.finished_at = "FAILED", _now()
        vr.result = {"error": "The rules changed while validating; validate again"}
        session.commit()
        return vr
    try:
        schema = schema_of(session, v)
        upload = session.get(EvidenceUpload, vr.upload_id)
        adapter = ExcelSourceAdapter(upload.filename, platform.storage.get(upload.storage_key), limits)
        ctx = context_for(session, v, rules)
        result = validate(rules, adapter.extract(schema.sheet_name, schema.header_row), ctx,
                          source={"upload_id": str(upload.id), "sheet": schema.sheet_name})
    except Exception as exc:  # noqa: BLE001 - a broken file or store must not leave the version stuck
        vr.status, vr.finished_at, vr.result = "FAILED", _now(), {"error": f"{type(exc).__name__}: {exc}"}
        _set_status(v, M.DRAFT)
        session.commit()
        return vr
    parent = session.get(MappingVersion, v.parent_version_id) if v.parent_version_id else None
    result["impact"] = {"counts": result.get("counts"), "rows_validated": (result.get("counts") or {}).get("total_rows"),
                        "rule_diff": rule_diff(rules_of(session, parent), rules) if parent else None,
                        "parent_version": parent.version if parent else None}
    vr.status, vr.result, vr.finished_at = result["status"], result, _now()
    _set_status(v, M.VALIDATION_FAILED if result["status"] == "BLOCKED" else M.READY_FOR_REVIEW)
    _event(session, v.tenant_id, "MappingValidationCompleted", definition,
           {"version": v.version, "validation_id": str(vr.id), "status": result["status"],
            "blocking_errors": result["blocking_errors"], "warnings": result["warnings"]})
    session.commit()
    return vr


def latest_validation(session: Session, version: MappingVersion) -> MappingValidationRun | None:
    return session.scalar(select(MappingValidationRun).where(MappingValidationRun.version_id == version.id)
                          .order_by(MappingValidationRun.created_at.desc()).limit(1))


# ---------------------------------------------------------------------------
# Submit, review, publish
# ---------------------------------------------------------------------------

def submit(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID, mapping_id: uuid.UUID,
           number: int, expected_lock: int, acknowledge_warnings: bool) -> MappingVersion:
    definition = get_definition(session, principal.tenant_id, run_id, mapping_id)
    v = get_version(session, definition, number, for_update=True)
    _check_lock(v, expected_lock)
    if v.status != M.READY_FOR_REVIEW.value:
        raise MappingInvalidTransition(f"Validate the version first (it is {v.status})")
    h = current_hash(session, v)
    vr = latest_validation(session, v)
    if vr is None or vr.content_hash != h or vr.status not in ("PASS", "WARNING"):
        raise MappingInvalidTransition("The latest validation does not match these rules; validate again")
    if vr.status == "WARNING" and not acknowledge_warnings:
        raise MappingValidationError("Acknowledge the warnings to submit",
                                     [FieldError("acknowledge_warnings", f"{vr.result['warnings']} warning(s)")])
    v.content_hash = h
    v.warnings_acknowledged = vr.status == "WARNING"
    v.submitted_by, v.submitted_at = principal.user_id, _now()
    _set_status(v, M.IN_REVIEW)
    session.flush()
    payload = _audit(platform.audit, session, principal, "mapping.submitted", v, definition,
                     {"validation_status": vr.status, "warnings_acknowledged": v.warnings_acknowledged})
    _event(session, principal.tenant_id, "MappingSubmittedForReview", definition, payload)
    session.commit()
    reviewers = [p.user_id for p in platform.people.search(principal.tenant_id, roles=APPROVER_ROLES, session=session)
                 if p.user_id != principal.user_id]
    if reviewers:
        platform.notify.send(Notification(
            tenant_id=principal.tenant_id, recipients=tuple(reviewers),
            subject=f"Mapping {definition.domain} v{v.version} is waiting for your review",
            body=f"{principal.name} submitted a mapping for review.",
            link=f"/runs/{run_id}/mappings/{definition.id}/versions/{v.version}"))
    return v


REVIEW_TARGET = {"APPROVE": M.APPROVED, "RETURN": M.DRAFT, "REJECT": M.RETIRED}
REVIEW_EVENT = {"APPROVE": "MappingApproved", "RETURN": "MappingReturned", "REJECT": "MappingRejected"}
REVIEW_ACTION = {"APPROVE": "mapping.approved", "RETURN": "mapping.returned", "REJECT": "mapping.rejected"}


def review(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID, mapping_id: uuid.UUID,
           number: int, decision: str, reviewed_hash: str, comment: str | None) -> MappingVersion:
    if decision not in REVIEW_TARGET:
        raise MappingValidationError("Unknown decision", [FieldError("decision", "APPROVE, RETURN or REJECT")])
    comment = " ".join((comment or "").split()) or None
    if decision != "APPROVE" and not (comment and COMMENT_MIN <= len(comment) <= COMMENT_MAX):
        raise MappingValidationError("A comment is required",
                                     [FieldError("comment", f"Explain why ({COMMENT_MIN}-{COMMENT_MAX} characters)")])
    if comment and len(comment) > COMMENT_MAX:
        raise MappingValidationError("Comment too long", [FieldError("comment", f"At most {COMMENT_MAX} characters")])
    definition = get_definition(session, principal.tenant_id, run_id, mapping_id)
    v = get_version(session, definition, number, for_update=True)
    if v.status != M.IN_REVIEW.value:
        raise MappingInvalidTransition(f"Version {v.version} is {v.status}, not in review")
    if reviewed_hash != v.content_hash:
        raise MappingConflict("You reviewed a different content hash; reload the version",
                              [FieldError("content_hash", "Does not match the submitted version")])
    if v.submitted_by == principal.user_id:
        raise SeparationOfDuties("You submitted this version; another reviewer must decide")
    session.add(MappingApproval(tenant_id=principal.tenant_id, version_id=v.id, decision=decision, comment=comment,
                                content_hash=v.content_hash, actor_id=principal.user_id, actor_name=principal.name))
    if decision == "APPROVE":
        v.approved_by, v.approved_at = principal.user_id, _now()
    if decision == "RETURN":
        v.content_hash = None
        v.warnings_acknowledged = False
    _set_status(v, REVIEW_TARGET[decision], comment if decision != "APPROVE" else None)
    session.flush()
    payload = _audit(platform.audit, session, principal, REVIEW_ACTION[decision], v, definition,
                     {"decision": decision, "comment": comment, "reviewed_hash": reviewed_hash})
    _event(session, principal.tenant_id, REVIEW_EVENT[decision], definition, payload)
    session.commit()
    if v.submitted_by:
        platform.notify.send(Notification(
            tenant_id=principal.tenant_id, recipients=(v.submitted_by,),
            subject=f"Mapping {definition.domain} v{v.version}: {decision.lower()}",
            body=comment or "Approved.", link=f"/runs/{run_id}/mappings/{definition.id}/versions/{v.version}"))
    return v


def publish(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID, mapping_id: uuid.UUID,
            number: int, expected_lock: int) -> MappingVersion:
    definition = get_definition(session, principal.tenant_id, run_id, mapping_id)
    session.execute(select(MappingDefinition.id).where(MappingDefinition.id == definition.id).with_for_update())
    v = get_version(session, definition, number, for_update=True)
    _check_lock(v, expected_lock)
    if v.status != M.APPROVED.value:
        raise MappingInvalidTransition(f"Only an approved version can be published (it is {v.status})")
    previous = session.scalar(select(MappingVersion).where(MappingVersion.definition_id == definition.id,
                                                           MappingVersion.status == M.PUBLISHED.value)
                              .with_for_update())
    if previous is not None:
        _set_status(previous, M.SUPERSEDED, f"Superseded by version {v.version}")
        session.flush()
    v.published_by, v.published_at = principal.user_id, _now()
    _set_status(v, M.PUBLISHED)
    session.flush()
    payload = _audit(platform.audit, session, principal, "mapping.published", v, definition,
                     {"supersedes": previous.version if previous else None})
    _event(session, principal.tenant_id, "MappingPublished", definition, payload)
    if previous is not None:
        _event(session, principal.tenant_id, "MappingSuperseded", definition,
               {"version": previous.version, "superseded_by": v.version})
    session.commit()
    return v


# ---------------------------------------------------------------------------
# Optional AI suggestions (headers + 5 sample rows only)
# ---------------------------------------------------------------------------

AI_SYSTEM = ("You map spreadsheet columns of Telco product evidence to canonical fields. Only suggest a target from "
             "the list given. If unsure, leave the column out. Never invent fields.")


def ai_suggest(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID, mapping_id: uuid.UUID,
               number: int, expected_lock: int) -> dict[str, Any]:
    definition = get_definition(session, principal.tenant_id, run_id, mapping_id)
    v = get_version(session, definition, number, for_update=True)
    if MappingStatus(v.status) not in EDITABLE:
        raise MappingNotEditable(f"Version {v.version} is {v.status}")
    _check_lock(v, expected_lock)
    rules = rules_of(session, v)
    ctx = context_for(session, v, rules)
    used = {c for r in rules for c in r.columns_used()}
    taken = {(r.target_entity, r.target_field) for r in rules if r.target_entity}
    columns = [c for c in _samples_for(session, schema_of(session, v)) if c["header"] not in used]
    entities = set(DOMAINS[EvidenceDomain(definition.domain)].canonical_entities) | {
        r.target_entity for r in rules if r.target_entity}
    targets = [f"{e['name']}.{f['name']}" for e in ctx.canonical["entities"] if e["name"] in entities
               for f in e["fields"] if (e["name"], f["name"]) not in taken]
    if not columns or not targets:
        return {"added": 0, "source": "none", "version": v}
    schema = {
        "type": "object", "required": ["suggestions"], "additionalProperties": False,
        "properties": {"suggestions": {"type": "array", "maxItems": len(columns), "items": {
            "type": "object", "required": ["column", "target"], "additionalProperties": False,
            "properties": {"column": {"enum": [c["header"] for c in columns]}, "target": {"enum": targets},
                           "reason": {"type": "string", "maxLength": 300}}}}},
    }
    prompt = json.dumps({"columns": [{"header": c["header"], "type": c["type"], "samples": c["samples"][:5]}
                                     for c in columns], "targets": targets}, ensure_ascii=False)
    result = run_ai_task(platform.ai, AIRequest(task="mapping_suggestion", system=AI_SYSTEM, prompt=prompt,
                                                schema=schema, max_tokens=2000),
                         fallback=lambda: {"suggestions": []})
    lookups = _selectable_refs(session, principal.tenant_id, ArtifactKind.LOOKUP)
    transforms = _selectable_refs(session, principal.tenant_id, ArtifactKind.TRANSFORM)
    by_header = {c["header"]: c for c in columns}
    next_n = max([int(r.key[1:]) for r in rules if r.key[1:].isdigit()] or [0]) + 1
    added: list[Rule] = []
    seen_cols: set[str] = set()
    for s in result.output["suggestions"]:
        entity, field = s["target"].split(".", 1)
        if s["column"] in seen_cols or (entity, field) in taken:
            continue
        rule = choose_rule(f"R{next_n}", by_header[s["column"]], entity, field, ctx.canonical, ProposedBy.AI,
                           lookups=lookups, transforms=transforms)
        if rule is None:
            continue
        added.append(rule)
        seen_cols.add(s["column"])
        taken.add((entity, field))
        next_n += 1
    if added:
        if v.status != M.DRAFT.value:
            _set_status(v, M.DRAFT)
        else:
            v.lock_version += 1
        session.flush()
        _replace_rules(session, v, rules + added)
        _audit(platform.audit, session, principal, "mapping.ai_suggested", v, definition,
               {"added": [r.key for r in added], "source": result.source, "model": result.model})
    session.commit()
    return {"added": len(added), "source": result.source, "version": v}


# ---------------------------------------------------------------------------
# Apply (background job): staged records + relationships + field lineage
# ---------------------------------------------------------------------------

def execution_key(upload_sha256: str, version_id: uuid.UUID) -> str:
    return "sha256:" + hashlib.sha256(f"{upload_sha256}:{version_id}".encode()).hexdigest()


@dataclass
class ApplyResult:
    execution: MappingExecution
    existing: bool


def request_apply(session: Session, platform: Platform, principal: Principal, run_id: uuid.UUID,
                  upload_id: uuid.UUID, mapping_version_id: uuid.UUID, idempotency_key: str | None) -> ApplyResult:
    run = _run(session, principal.tenant_id, run_id)
    upload = session.scalar(select(EvidenceUpload).where(EvidenceUpload.id == upload_id,
                                                         EvidenceUpload.run_id == run.id,
                                                         EvidenceUpload.tenant_id == principal.tenant_id))
    if upload is None:
        raise MappingNotFound("Upload not found")
    v = session.scalar(select(MappingVersion).where(MappingVersion.id == mapping_version_id,
                                                    MappingVersion.tenant_id == principal.tenant_id))
    if v is None:
        raise MappingNotFound("Mapping version not found")
    definition = session.get(MappingDefinition, v.definition_id)
    if definition.source_id != upload.source_id:
        raise MappingConflict("This mapping belongs to another source")
    if v.status != M.PUBLISHED.value:
        raise MappingInvalidTransition(f"Only a published mapping can be applied (version {v.version} is {v.status})")
    if upload.status != UploadStatus.READY.value or upload.schema_version_id is None or not upload.storage_key:
        raise MappingConflict("Confirm the file's sheet and header row first")
    compat = compatibility(session, v, session.get(SourceSchemaVersion, upload.schema_version_id))
    if not compat["compatible"]:
        raise MappingConflict("The file does not have every column the mapping uses",
                              [FieldError("upload_id", "Missing: " + ", ".join(compat["missing_columns"]))])
    key = execution_key(upload.sha256, v.id)
    if idempotency_key:
        other = session.scalar(select(MappingExecution).where(MappingExecution.tenant_id == principal.tenant_id,
                                                              MappingExecution.idempotency_key == idempotency_key))
        if other is not None and other.execution_key != key:
            raise MappingConflict("This Idempotency-Key was used for a different request")
    existing = session.scalar(select(MappingExecution).where(MappingExecution.tenant_id == principal.tenant_id,
                                                             MappingExecution.execution_key == key).with_for_update())
    if existing is not None and existing.status != "FAILED":
        session.commit()
        return ApplyResult(existing, True)
    if existing is not None:  # retry a failed execution in place
        existing.status, existing.error, existing.requested_by = "QUEUED", None, principal.user_id
        execution = existing
    else:
        execution = MappingExecution(tenant_id=principal.tenant_id, run_id=run.id, upload_id=upload.id,
                                     mapping_version_id=v.id, execution_key=key, idempotency_key=idempotency_key,
                                     status="QUEUED", requested_by=principal.user_id)
        session.add(execution)
    try:
        session.flush()
    except IntegrityError:  # a concurrent identical request won
        session.rollback()
        return ApplyResult(session.scalar(select(MappingExecution).where(
            MappingExecution.tenant_id == principal.tenant_id, MappingExecution.execution_key == key)), True)
    _audit(platform.audit, session, principal, "mapping.apply_requested", v, definition,
           {"execution_id": str(execution.id), "upload_id": str(upload.id), "execution_key": key})
    session.commit()
    execution.job_id = platform.jobs.enqueue(EXECUTE_JOB, str(execution.id))
    session.commit()
    return ApplyResult(execution, False)


def run_execution(session: Session, platform: Platform, limits: FileLimits, execution_id: uuid.UUID) -> MappingExecution:
    ex = session.scalar(select(MappingExecution).where(MappingExecution.id == execution_id).with_for_update())
    if ex is None:
        raise MappingNotFound("Execution not found")
    if ex.status not in ("QUEUED",):
        session.commit()
        return ex
    ex.status = "RUNNING"
    session.commit()
    v = session.get(MappingVersion, ex.mapping_version_id)
    definition = session.get(MappingDefinition, v.definition_id)
    upload = session.get(EvidenceUpload, ex.upload_id)
    schema = session.get(SourceSchemaVersion, upload.schema_version_id)
    try:
        rules = rules_of(session, v)
        ctx = context_for(session, v, rules)
        adapter = ExcelSourceAdapter(upload.filename, platform.storage.get(upload.storage_key), limits)
        result = execute(rules, adapter.extract(schema.sheet_name, schema.header_row), ctx,
                         source={"upload_id": str(upload.id), "sheet": schema.sheet_name})
        _write_staged(session, ex, v, definition, result)
    except Exception as exc:  # noqa: BLE001 - record the failure; the request can be retried
        session.rollback()
        ex = session.get(MappingExecution, execution_id)
        ex.status, ex.error, ex.finished_at = "FAILED", f"{type(exc).__name__}: {exc}"[:2000], _now()
        session.commit()
        return ex
    ex.status, ex.finished_at = "SUCCEEDED", _now()
    ex.counts, ex.rejected_rows, ex.conflicts = result.counts(), result.rejected_rows, result.conflicts
    ex.output_hash = output_hash(result)
    payload = {"execution_id": str(ex.id), "upload_id": str(upload.id), "mapping_version": v.version,
               "counts": ex.counts, "output_hash": ex.output_hash}
    platform.audit.record(AuditRecord(tenant_id=ex.tenant_id, actor_id="system:mapping", action="mapping.applied",
                                      entity_type="mapping_execution", entity_id=str(ex.id), payload=payload),
                          session=session)
    _event(session, ex.tenant_id, "MappingExecutionCompleted", definition, payload)
    session.commit()
    return ex


def _write_staged(session: Session, ex: MappingExecution, v: MappingVersion, definition: MappingDefinition,
                  result) -> None:
    records, lineage = [], []
    for (entity, key), rec in sorted(result.records.items()):
        rid = uuid.uuid4()
        records.append({"id": rid, "tenant_id": ex.tenant_id, "execution_id": ex.id, "entity": entity,
                        "record_key": key, "values": rec.values, "source_rows": rec.rows})
        for field_name, lin in sorted(rec.lineage.items()):
            lineage.append({
                "tenant_id": ex.tenant_id, "execution_id": ex.id, "staged_record_id": rid,
                "canonical_entity": entity, "canonical_field": field_name,
                "canonical_value": json.dumps(rec.values[field_name], ensure_ascii=False),
                "source_system": f"EXCEL_UPLOAD:{definition.domain}", "source_key": key,
                "source_field": lin["source_field"], "source_value": lin["source_value"],
                "upload_id": uuid.UUID(lin["upload_id"]), "sheet": lin["sheet"], "source_row": lin["row"],
                "mapping_rule_key": lin["rule_key"], "mapping_type": lin["mapping_type"],
                "lookup_ref": lin["lookup_ref"], "transform_ref": lin["transform_ref"],
                "crosswalk_ref": lin["crosswalk_ref"], "mapping_version_id": v.id})
    edges = [{"tenant_id": ex.tenant_id, "execution_id": ex.id, "name": e.name, "from_entity": e.from_entity,
              "from_key": e.from_key, "to_entity": e.to_entity, "to_key": e.to_key, "source_row": e.row,
              "rule_key": e.rule_key} for e in result.edges]
    if records:
        session.execute(insert(StagedRecord), records)
    if lineage:
        session.execute(insert(FieldLineage), lineage)
    if edges:
        session.execute(insert(StagedRelationship), edges)


def get_execution(session: Session, tenant_id: str, run_id: uuid.UUID, execution_id: uuid.UUID) -> MappingExecution:
    ex = session.scalar(select(MappingExecution).where(MappingExecution.id == execution_id,
                                                       MappingExecution.run_id == run_id,
                                                       MappingExecution.tenant_id == tenant_id))
    if ex is None:
        raise MappingNotFound("Execution not found")
    return ex


def staged_records(session: Session, ex: MappingExecution, entity: str | None, limit: int, offset: int):
    stmt = select(StagedRecord).where(StagedRecord.execution_id == ex.id, StagedRecord.tenant_id == ex.tenant_id)
    if entity:
        stmt = stmt.where(StagedRecord.entity == entity)
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    records = list(session.scalars(stmt.order_by(StagedRecord.entity, StagedRecord.record_key)
                                   .limit(limit).offset(offset)))
    lineage: dict[uuid.UUID, list[FieldLineage]] = {}
    if records:
        for lin in session.scalars(select(FieldLineage).where(
                FieldLineage.staged_record_id.in_([r.id for r in records])).order_by(FieldLineage.id)):
            lineage.setdefault(lin.staged_record_id, []).append(lin)
    return total, records, lineage


def approvals_of(session: Session, version: MappingVersion) -> list[MappingApproval]:
    return list(session.scalars(select(MappingApproval).where(MappingApproval.version_id == version.id)
                                .order_by(MappingApproval.created_at)))


def executions_of(session: Session, version: MappingVersion) -> list[MappingExecution]:
    return list(session.scalars(select(MappingExecution).where(MappingExecution.mapping_version_id == version.id)
                                .order_by(MappingExecution.created_at.desc())))
