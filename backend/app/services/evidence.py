"""Evidence Hub service (screen 03): domain selection, Excel upload intake, sheet discovery, source schemas.

Upload flow (CLAUDE.md rule 12):
1. ``upload_file``: quick checks on the bytes (type, size, macros, password, damage, sheet count).
   Failures are rejected at once with reasons. Passing files go to QUARANTINE storage; a background
   job is queued; the upload is PROCESSING.
2. ``process_upload`` (job): malware scan, re-check, read sheets (row/column limits, personal data).
   Clean -> promoted to CHECKED storage, READY, ``RawEvidenceLanded``. Otherwise REJECTED and the file
   is deleted (only the record and reasons remain).
3. ``confirm_schema``: the user picks sheet + header row; the column structure becomes an immutable
   source schema version (same headers -> same version; changed headers -> new version).
"""

import hashlib
import re
import uuid
from dataclasses import dataclass
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.adapters.registry import Platform
from app.config import Settings
from app.db.models import EvidenceRequest, EvidenceSource, EvidenceUpload, Run, SourceSchemaVersion
from app.domain.artifacts import ArtifactKind, canonical_json, format_ref
from app.domain.common import FieldError
from app.domain.evidence import (
    DOMAINS,
    DomainStatus,
    EvidenceDomain,
    SourceType,
    UploadStatus,
    domain_status,
    propose_domains,
    proposal_reasons,
)
from app.domain.evidence_templates import match_template
from app.domain.excel_checks import FileLimits, Problem, check_workbook_file
from app.domain.excel_discovery import discover_workbook
from app.domain.runs import RunState
from app.ports.audit import AuditLog, AuditRecord
from app.ports.auth import Principal
from app.ports.storage import StorageArea
from app.services import artifacts as registry
from app.services.errors import ServiceError
from app.services.excel_templates import build_template_workbook
from app.services.outbox import emit

PROCESS_JOB = "app.jobs.evidence.process_upload"
EDITABLE_STATES = frozenset({RunState.CONFIGURING})


class EvidenceError(ServiceError):
    code = "evidence_error"


class EvidenceValidationError(EvidenceError):
    status_code = 422
    code = "validation_failed"


class EvidenceFileRejected(EvidenceError):
    status_code = 422
    code = "file_rejected"


class EvidenceNotFound(EvidenceError):
    status_code = 404
    code = "not_found"


class EvidenceNotEditable(EvidenceError):
    status_code = 409
    code = "not_editable"


class EvidenceVersionConflict(EvidenceError):
    status_code = 409
    code = "version_conflict"


class EvidenceNameTaken(EvidenceError):
    status_code = 409
    code = "name_taken"


class DomainNotSelected(EvidenceError):
    status_code = 409
    code = "domain_not_selected"


class UploadNotReady(EvidenceError):
    status_code = 409
    code = "not_ready"


def limits_from(settings: Settings) -> FileLimits:
    return FileLimits(max_bytes=settings.upload_max_bytes, max_sheets=settings.upload_max_sheets,
                      max_rows=settings.upload_max_rows, max_columns=settings.upload_max_columns,
                      max_unpacked_bytes=settings.upload_max_unpacked_bytes, max_parts=settings.upload_max_parts)


def schema_fingerprint(headers: list[str]) -> str:
    """Identity of a column structure: the ordered, normalised header names.

    Inferred types are kept with the version for information but are not part of its identity
    (an optional column that happens to be empty in one file must not create a new version).
    """
    norm = [" ".join(h.split()) for h in headers]
    return "sha256:" + hashlib.sha256(canonical_json({"headers": norm})).hexdigest()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _run(session: Session, tenant_id: str, run_id: uuid.UUID, *, for_update: bool = False) -> Run:
    stmt = select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    run = session.scalar(stmt)
    if run is None:
        raise EvidenceNotFound("Run not found")
    return run


def evidence_locked(session: Session, run: Run) -> str | None:
    """Why evidence cannot change now, or None. Evidence is editable while configuring, and again in REVIEW while
    the run has an evidence request waiting for a return (step 13: progressive evidence collection)."""
    if run.state in EDITABLE_STATES:
        return None
    if run.state == RunState.REVIEW.value and session.scalar(select(EvidenceRequest.id).where(
            EvidenceRequest.run_id == run.id, EvidenceRequest.tenant_id == run.tenant_id,
            EvidenceRequest.status.in_(("OPEN", "IN_PROGRESS", "REJECTED"))).limit(1)):
        return None
    return (f"Evidence can only be changed while the run is configuring or has an open evidence request "
            f"(now {run.state})")


def is_editable(session: Session, run: Run) -> bool:
    return evidence_locked(session, run) is None


def _require_editable(session: Session, run: Run) -> None:
    reason = evidence_locked(session, run)
    if reason:
        raise EvidenceNotEditable(reason)


def selected_domains(run: Run) -> list[EvidenceDomain]:
    if run.evidence_domains is not None:
        return [EvidenceDomain(d) for d in run.evidence_domains if d in DOMAINS]
    return propose_domains(list(run.objectives))


def _domain(value: str) -> EvidenceDomain:
    try:
        return EvidenceDomain(value)
    except ValueError as exc:
        raise EvidenceNotFound(f"Unknown evidence domain {value}") from exc


def _audit(audit: AuditLog, session: Session, principal: Principal, action: str, entity_type: str,
           entity_id: str, payload: dict[str, Any]) -> None:
    audit.record(AuditRecord(
        tenant_id=principal.tenant_id, actor_id=principal.user_id, actor_roles=principal.roles,
        auth_context=principal.auth_context, action=action, entity_type=entity_type, entity_id=entity_id,
        payload=payload), session=session)


def _upload_payload(u: EvidenceUpload) -> dict[str, Any]:
    return {"upload_id": str(u.id), "run_id": str(u.run_id), "source_id": str(u.source_id), "domain": u.domain,
            "filename": u.filename, "size_bytes": u.size_bytes, "sha256": u.sha256, "status": u.status}


def template_for(session: Session, tenant_id: str, domain: EvidenceDomain) -> tuple[str, dict] | None:
    """(ref, content) of the domain's published template, or None if there is none."""
    try:
        artifact, v = registry.resolve(session, tenant_id, DOMAINS[domain].template_key,
                                       kind=ArtifactKind.EVIDENCE_TEMPLATE)
    except registry.ArtifactNotFound:
        return None
    return format_ref(artifact.key, registry.version_of(v)), v.content


# ---------------------------------------------------------------------------
# Overview and domain selection
# ---------------------------------------------------------------------------

@dataclass
class DomainView:
    domain: EvidenceDomain
    selected: bool
    proposed: bool
    reasons: list[str]
    status: DomainStatus
    uploads: list[EvidenceUpload]
    template_ref: str | None
    sources: list[EvidenceSource]


@dataclass
class EvidenceOverview:
    run: Run
    selection_confirmed: bool
    editable: bool
    domains: list[DomainView]


def overview(session: Session, tenant_id: str, run_id: uuid.UUID) -> EvidenceOverview:
    run = _run(session, tenant_id, run_id)
    selected = set(selected_domains(run))
    proposed = set(propose_domains(list(run.objectives)))
    reasons = proposal_reasons(list(run.objectives))
    uploads = list(session.scalars(select(EvidenceUpload).where(
        EvidenceUpload.tenant_id == tenant_id, EvidenceUpload.run_id == run.id)
        .order_by(EvidenceUpload.created_at.desc())))
    by_domain: dict[str, list[EvidenceUpload]] = {}
    for u in uploads:
        by_domain.setdefault(u.domain, []).append(u)
    views = []
    for d in DOMAINS:
        ups = by_domain.get(d.value, [])
        tpl = template_for(session, tenant_id, d)
        views.append(DomainView(
            domain=d, selected=d in selected, proposed=d in proposed, reasons=reasons.get(d, []),
            status=domain_status([(u.status, u.schema_version_id is not None) for u in ups]),
            uploads=ups, template_ref=tpl[0] if tpl else None, sources=sources_of(session, run, d)))
    return EvidenceOverview(run=run, selection_confirmed=run.evidence_domains is not None,
                            editable=is_editable(session, run), domains=views)


def set_domains(session: Session, audit: AuditLog, principal: Principal, run_id: uuid.UUID,
                domains: list[str], expected_version: int) -> Run:
    run = _run(session, principal.tenant_id, run_id, for_update=True)
    _require_editable(session, run)
    if run.version != expected_version:
        raise EvidenceVersionConflict("This run was changed by someone else. Reload and try again.")
    unknown = [d for d in domains if d not in DOMAINS]
    if unknown:
        raise EvidenceValidationError("Unknown evidence domain", [FieldError("domains", f"Unknown: {', '.join(unknown)}")])
    chosen = [d.value for d in DOMAINS if d.value in set(domains)]  # screen order, no duplicates
    if not chosen:
        raise EvidenceValidationError("Select at least one evidence domain",
                                      [FieldError("domains", "Select at least one evidence domain")])
    before = [d.value for d in selected_domains(run)]
    run.evidence_domains = chosen
    run.version += 1
    run.updated_at = datetime.now(UTC)
    session.flush()
    proposed = [d.value for d in propose_domains(list(run.objectives))]
    payload = {"run_code": run.run_code, "before": before, "after": chosen, "proposed": proposed,
               "added_to_proposal": [d for d in chosen if d not in proposed],
               "removed_from_proposal": [d for d in proposed if d not in chosen]}
    _audit(audit, session, principal, "run.evidence_domains_set", "run", str(run.id), payload)
    emit(session, tenant_id=principal.tenant_id, event_type="EvidenceDomainsConfirmed", aggregate_type="run",
         aggregate_id=str(run.id), payload=payload)
    session.commit()
    return run


# ---------------------------------------------------------------------------
# Upload intake
# ---------------------------------------------------------------------------

def sources_of(session: Session, run: Run, domain: EvidenceDomain) -> list[EvidenceSource]:
    return list(session.scalars(select(EvidenceSource).where(
        EvidenceSource.run_id == run.id, EvidenceSource.tenant_id == run.tenant_id,
        EvidenceSource.domain == domain.value).order_by(EvidenceSource.created_at, EvidenceSource.name)))


def _excel_source(session: Session, principal: Principal, run: Run, domain: EvidenceDomain,
                  source_id: uuid.UUID | None = None) -> EvidenceSource:
    """The chosen source, or the domain's first Excel source (created on first use)."""
    if source_id is not None:
        src = session.scalar(select(EvidenceSource).where(
            EvidenceSource.id == source_id, EvidenceSource.run_id == run.id, EvidenceSource.domain == domain.value,
            EvidenceSource.tenant_id == run.tenant_id))
        if src is None:
            raise EvidenceNotFound("Evidence source not found for this domain")
        return src
    src = next((x for x in sources_of(session, run, domain) if x.source_type == SourceType.EXCEL_UPLOAD.value), None)
    if src is None:
        src = EvidenceSource(tenant_id=run.tenant_id, run_id=run.id, domain=domain.value,
                             source_type=SourceType.EXCEL_UPLOAD.value, name=f"Excel · {DOMAINS[domain].label}",
                             created_by=principal.user_id, created_at=datetime.now(UTC))
        session.add(src)
        session.flush()
    return src


SYSTEM_RE = re.compile(r"^[A-Z][A-Z0-9_]{1,39}$")


def add_source(session: Session, audit: AuditLog, principal: Principal, run_id: uuid.UUID, domain_value: str,
               name: str, system: str) -> EvidenceSource:
    """Another Excel source for a domain, e.g. a second CRM export from another system."""
    domain = _domain(domain_value)
    run = _run(session, principal.tenant_id, run_id)
    _require_editable(session, run)
    if domain not in selected_domains(run):
        raise DomainNotSelected(f"{DOMAINS[domain].label} is not selected for this run")
    name = " ".join((name or "").split())
    system = (system or "").strip().upper()
    errors = []
    if not 3 <= len(name) <= 120:
        errors.append(FieldError("name", "Name must be 3-120 characters"))
    if not SYSTEM_RE.match(system):
        errors.append(FieldError("system", "System code: upper-case letters, digits and _ (e.g. SALESFORCE)"))
    if errors:
        raise EvidenceValidationError("The source is not valid", errors)
    _excel_source(session, principal, run, domain)  # make sure the default source exists first
    if any(x.name.casefold() == name.casefold() for x in sources_of(session, run, domain)):
        raise EvidenceNameTaken("A source with this name already exists", [FieldError("name", "Name in use")])
    src = EvidenceSource(tenant_id=run.tenant_id, run_id=run.id, domain=domain.value,
                         source_type=SourceType.EXCEL_UPLOAD.value, name=name, system=system,
                         created_by=principal.user_id, created_at=datetime.now(UTC))
    session.add(src)
    session.flush()
    _audit(audit, session, principal, "evidence.source_added", "evidence_source", str(src.id),
           {"run_id": str(run.id), "domain": domain.value, "name": name, "system": system})
    session.commit()
    return src


def _reasons(problems: list[Problem]) -> list[dict[str, str]]:
    return [{"code": p.code, "message": p.message} for p in problems]


@dataclass
class UploadResult:
    upload: EvidenceUpload
    duplicate: bool


def upload_file(session: Session, platform: Platform, limits: FileLimits, principal: Principal,
                run_id: uuid.UUID, domain_value: str, filename: str, data: bytes,
                source_id: uuid.UUID | None = None) -> UploadResult:
    domain = _domain(domain_value)
    run = _run(session, principal.tenant_id, run_id)
    _require_editable(session, run)
    if domain not in selected_domains(run):
        raise DomainNotSelected(f"{DOMAINS[domain].label} is not selected for this run")
    filename = (filename or "upload.xlsx").replace("\\", "/").rsplit("/", 1)[-1][:255]
    digest = hashlib.sha256(data).hexdigest()

    problems = check_workbook_file(filename, data, limits)
    if problems:
        _audit(platform.audit, session, principal, "evidence.upload_rejected", "run", str(run.id),
               {"domain": domain.value, "filename": filename, "size_bytes": len(data), "sha256": digest,
                "reasons": _reasons(problems)})
        session.commit()
        raise EvidenceFileRejected("The file was not accepted",
                                   [FieldError("file", p.message) for p in problems])

    source = _excel_source(session, principal, run, domain, source_id)
    existing = session.scalar(select(EvidenceUpload).where(
        EvidenceUpload.source_id == source.id, EvidenceUpload.sha256 == digest,
        EvidenceUpload.status != UploadStatus.REJECTED.value))
    if existing is not None:
        session.commit()
        return UploadResult(existing, duplicate=True)

    stored = platform.storage.put(principal.tenant_id, filename, data, StorageArea.QUARANTINE)
    upload = EvidenceUpload(
        tenant_id=principal.tenant_id, run_id=run.id, source_id=source.id, domain=domain.value, filename=filename,
        size_bytes=len(data), sha256=digest, storage_key=stored.key, status=UploadStatus.PROCESSING.value,
        reasons=[], uploaded_by=principal.user_id, uploaded_by_name=principal.name)
    session.add(upload)
    try:
        session.flush()
    except IntegrityError:  # the same file uploaded twice at the same moment
        session.rollback()
        platform.storage.delete(stored.key)
        dup = session.scalar(select(EvidenceUpload).where(
            EvidenceUpload.source_id == source.id, EvidenceUpload.sha256 == digest,
            EvidenceUpload.status != UploadStatus.REJECTED.value))
        if dup is None:
            raise
        return UploadResult(dup, duplicate=True)
    _audit(platform.audit, session, principal, "evidence.uploaded", "evidence_upload", str(upload.id),
           _upload_payload(upload))
    session.commit()
    upload.job_id = platform.jobs.enqueue(PROCESS_JOB, str(upload.id))
    session.commit()
    return UploadResult(upload, duplicate=False)


def _reject(session: Session, platform: Platform, upload: EvidenceUpload, reasons: list[dict[str, str]]) -> None:
    if upload.storage_key:
        platform.storage.delete(upload.storage_key)
    upload.storage_key = None
    upload.status = UploadStatus.REJECTED.value
    upload.reasons = reasons
    upload.processed_at = datetime.now(UTC)
    upload.discovery = None
    payload = _upload_payload(upload) | {"reasons": reasons}
    platform.audit.record(AuditRecord(
        tenant_id=upload.tenant_id, actor_id="system:intake", action="evidence.upload_rejected",
        entity_type="evidence_upload", entity_id=str(upload.id), payload=payload), session=session)
    emit(session, tenant_id=upload.tenant_id, event_type="EvidenceUploadRejected", aggregate_type="evidence_upload",
         aggregate_id=str(upload.id), payload=payload)


def process_upload(session: Session, platform: Platform, limits: FileLimits, upload_id: uuid.UUID) -> EvidenceUpload:
    """Background step: scan, re-check and read a quarantined upload. Safe to run twice."""
    upload = session.scalar(select(EvidenceUpload).where(EvidenceUpload.id == upload_id).with_for_update())
    if upload is None:
        raise EvidenceNotFound("Upload not found")
    if upload.status != UploadStatus.PROCESSING.value:
        session.commit()  # already processed (job ran twice): release the row lock, change nothing
        return upload
    data = platform.storage.get(upload.storage_key)

    scan = platform.scanner.scan(data, filename=upload.filename)
    upload.scan = {"clean": scan.clean, "engine": scan.engine, "signature": scan.signature,
                   "scanned_at": datetime.now(UTC).isoformat()}
    if not scan.clean:
        _reject(session, platform, upload, [{"code": "malware",
                                             "message": f"The file failed the malware scan ({scan.signature})."}])
        session.commit()
        return upload

    problems = check_workbook_file(upload.filename, data, limits)
    info = None
    if not problems:
        info = discover_workbook(data, limits)
        problems = info.problems
    if problems:
        _reject(session, platform, upload, _reasons(problems))
        session.commit()
        return upload
    if info.default_sheet is None:
        _reject(session, platform, upload, [{
            "code": "no_data", "message": "No sheet has a header row with data under it."}])
        session.commit()
        return upload

    discovery = info.to_dict()
    tpl = template_for(session, upload.tenant_id, EvidenceDomain(upload.domain))
    if tpl:
        discovery["template_ref"] = tpl[0]
        for sheet in discovery["sheets"]:
            sheet["template_match"] = match_template(tpl[1], [c["header"] for c in sheet["columns"]])
    promoted = platform.storage.promote(upload.storage_key)
    upload.storage_key = promoted.key
    upload.discovery = discovery
    upload.status = UploadStatus.READY.value
    upload.processed_at = datetime.now(UTC)
    payload = _upload_payload(upload) | {"default_sheet": info.default_sheet,
                                         "sheets": [s.name for s in info.sheets], "scan_engine": scan.engine}
    platform.audit.record(AuditRecord(
        tenant_id=upload.tenant_id, actor_id="system:intake", action="evidence.upload_checked",
        entity_type="evidence_upload", entity_id=str(upload.id), payload=payload), session=session)
    emit(session, tenant_id=upload.tenant_id, event_type="RawEvidenceLanded", aggregate_type="evidence_upload",
         aggregate_id=str(upload.id), payload=payload)
    session.commit()
    return upload


# ---------------------------------------------------------------------------
# Reading uploads, preview, confirming the source schema
# ---------------------------------------------------------------------------

def get_upload(session: Session, tenant_id: str, run_id: uuid.UUID, upload_id: uuid.UUID, *,
               for_update: bool = False) -> EvidenceUpload:
    stmt = select(EvidenceUpload).where(EvidenceUpload.id == upload_id, EvidenceUpload.run_id == run_id,
                                        EvidenceUpload.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    upload = session.scalar(stmt)
    if upload is None:
        raise EvidenceNotFound("Upload not found")
    return upload


def schema_version(session: Session, upload: EvidenceUpload) -> SourceSchemaVersion | None:
    if upload.schema_version_id is None:
        return None
    return session.get(SourceSchemaVersion, upload.schema_version_id)


def _sheet(session: Session, platform: Platform, limits: FileLimits, upload: EvidenceUpload, sheet: str,
           header_row: int | None) -> dict[str, Any]:
    if upload.status != UploadStatus.READY.value or not upload.storage_key:
        raise UploadNotReady(f"The file is {upload.status.lower()}; it cannot be previewed")
    if header_row is not None and not 1 <= header_row <= 50:
        raise EvidenceValidationError("Header row must be within the first 50 rows",
                                      [FieldError("header_row", "Choose a row between 1 and 50")])
    info = discover_workbook(platform.storage.get(upload.storage_key), limits, sheet=sheet, header_row=header_row)
    if not info.sheets:
        raise EvidenceValidationError("Unknown sheet", [FieldError("sheet", f"No sheet named '{sheet}'")])
    result = info.sheets[0].to_dict()
    tpl = template_for(session, upload.tenant_id, EvidenceDomain(upload.domain))
    if tpl:
        result["template_ref"] = tpl[0]
        result["template_match"] = match_template(tpl[1], [c["header"] for c in result["columns"]])
    return result


def preview_sheet(session: Session, platform: Platform, limits: FileLimits, principal: Principal,
                  run_id: uuid.UUID, upload_id: uuid.UUID, sheet: str, header_row: int | None) -> dict[str, Any]:
    upload = get_upload(session, principal.tenant_id, run_id, upload_id)
    return _sheet(session, platform, limits, upload, sheet, header_row)


@dataclass
class ConfirmResult:
    upload: EvidenceUpload
    schema: SourceSchemaVersion
    new_version: bool


def confirm_schema(session: Session, platform: Platform, limits: FileLimits, principal: Principal,
                   run_id: uuid.UUID, upload_id: uuid.UUID, sheet: str, header_row: int,
                   as_of: date | None = None) -> ConfirmResult:
    run = _run(session, principal.tenant_id, run_id)
    _require_editable(session, run)
    as_of = as_of or datetime.now(UTC).date()
    if as_of > datetime.now(UTC).date():
        raise EvidenceValidationError("The data cannot be from the future",
                                      [FieldError("as_of", "Choose today or an earlier date")])
    upload = get_upload(session, principal.tenant_id, run_id, upload_id, for_update=True)
    info = _sheet(session, platform, limits, upload, sheet, header_row)
    if info["problems"]:
        raise EvidenceValidationError("This sheet cannot be used",
                                      [FieldError("sheet", p) for p in info["problems"]])
    headers = [c["header"] for c in info["columns"]]
    fingerprint = schema_fingerprint(headers)

    # Serialise version numbering per source.
    session.execute(select(EvidenceSource.id).where(EvidenceSource.id == upload.source_id).with_for_update())
    existing = session.scalar(select(SourceSchemaVersion).where(
        SourceSchemaVersion.source_id == upload.source_id, SourceSchemaVersion.fingerprint == fingerprint))
    new_version = existing is None
    if existing is None:
        previous = session.scalar(select(SourceSchemaVersion).where(
            SourceSchemaVersion.source_id == upload.source_id).order_by(SourceSchemaVersion.version.desc()).limit(1))
        number = (session.scalar(select(func.max(SourceSchemaVersion.version)).where(
            SourceSchemaVersion.source_id == upload.source_id)) or 0) + 1
        existing = SourceSchemaVersion(
            tenant_id=upload.tenant_id, source_id=upload.source_id, version=number, fingerprint=fingerprint,
            sheet_name=sheet, header_row=info["header_row"],
            columns=[{"index": c["index"], "letter": c["letter"], "header": c["header"], "type": c["type"]}
                     for c in info["columns"]],
            template_ref=info.get("template_ref"), template_match=info.get("template_match"),
            first_upload_id=upload.id, created_by=principal.user_id)
        session.add(existing)
        session.flush()
        event = "SourceSchemaDiscovered" if previous is None else "SourceSchemaChanged"
        emit(session, tenant_id=upload.tenant_id, event_type=event, aggregate_type="evidence_source",
             aggregate_id=str(upload.source_id), payload={
                 "source_id": str(upload.source_id), "run_id": str(run.id), "domain": upload.domain,
                 "version": number, "fingerprint": fingerprint,
                 "previous_version": previous.version if previous else None,
                 "previous_fingerprint": previous.fingerprint if previous else None,
                 "added_columns": [h for h in headers if previous and h not in
                                   {c["header"] for c in previous.columns}],
                 "removed_columns": [c["header"] for c in previous.columns if c["header"] not in headers]
                 if previous else []})

    upload.schema_version_id = existing.id
    upload.confirmed_by = principal.user_id
    upload.confirmed_at = datetime.now(UTC)
    upload.as_of_date = as_of
    session.flush()
    _audit(platform.audit, session, principal, "evidence.schema_confirmed", "evidence_upload", str(upload.id),
           _upload_payload(upload) | {"sheet": sheet, "header_row": info["header_row"],
                                      "schema_version": existing.version, "fingerprint": fingerprint,
                                      "new_version": new_version})
    session.commit()
    return ConfirmResult(upload, existing, new_version)


# ---------------------------------------------------------------------------
# Template download
# ---------------------------------------------------------------------------

def template_file(session: Session, tenant_id: str, domain_value: str) -> tuple[str, bytes]:
    domain = _domain(domain_value)
    tpl = template_for(session, tenant_id, domain)
    if tpl is None:
        raise EvidenceNotFound(f"No published template for {DOMAINS[domain].label}")
    ref, content = tpl
    _, canonical = registry.resolve(session, tenant_id, content["canonicalSchema"],
                                    kind=ArtifactKind.CANONICAL_SCHEMA)
    lookups: dict[str, dict] = {}
    for col in content["columns"]:
        key = col.get("lookup")
        if key and key not in lookups:
            try:
                lookups[key] = registry.resolve(session, tenant_id, key, kind=ArtifactKind.LOOKUP)[1].content
            except registry.ArtifactNotFound:
                continue
    data = build_template_workbook(ref, DOMAINS[domain].label, content, canonical.content, lookups)
    filename = f"{ref.replace('@', '_v')}.xlsx"
    return filename, data

