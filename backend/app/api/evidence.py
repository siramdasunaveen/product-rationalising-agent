"""Evidence Hub (screen 03, wizard step 2): domains, governed templates, Excel uploads, source schemas."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, File, Request, Response, UploadFile, status
from sqlalchemy import select

from app.api.deps import CurrentUser, DbDep, PlatformDep, require_permission
from app.api.schemas import (
    DomainSelectionIn,
    ErrorResponse,
    EvidenceDomainInfo,
    EvidenceDomainOut,
    EvidenceOverviewOut,
    ProblemOut,
    SheetChoiceIn,
    SheetConfirmIn,
    SourceSchemaOut,
    SourceTypeOut,
    UploadOut,
    UploadSummary,
)
from app.db.models import EvidenceUpload, SourceSchemaVersion
from app.domain.evidence import AVAILABLE_SOURCE_TYPES, DOMAINS, SourceType
from app.domain.excel_checks import FileLimits
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import evidence as service

router = APIRouter(tags=["evidence"])

RunEditor = Annotated[Principal, Depends(require_permission(Permission.RUN_CREATE))]
Uploader = Annotated[Principal, Depends(require_permission(Permission.EVIDENCE_UPLOAD))]

ERRORS = {
    404: {"model": ErrorResponse, "description": "Run, domain or upload not found in your tenant"},
    409: {"model": ErrorResponse,
          "description": "not_editable, version_conflict, domain_not_selected or not_ready"},
    422: {"model": ErrorResponse, "description": "validation_failed or file_rejected (with reasons)"},
}
XLSX = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
SOURCE_TYPE_LABELS = {
    SourceType.API_CONNECTOR: "Live connector",
    SourceType.SECURE_QUERY: "Secure query",
    SourceType.SCHEDULED_EXTRACT: "Scheduled extract",
    SourceType.EXCEL_UPLOAD: "Excel upload",
}


def _limits(request: Request) -> FileLimits:
    return service.limits_from(request.app.state.settings)


def _summary(u: EvidenceUpload, schema: SourceSchemaVersion | None = None) -> UploadSummary:
    return UploadSummary(
        id=u.id, domain=u.domain, filename=u.filename, size_bytes=u.size_bytes, status=u.status,
        reasons=[ProblemOut(**r) for r in u.reasons], uploaded_by_name=u.uploaded_by_name, uploaded_at=u.created_at,
        processed_at=u.processed_at, default_sheet=(u.discovery or {}).get("default_sheet"),
        schema_version=schema.version if schema else None, confirmed_at=u.confirmed_at)


def _schema_out(s: SourceSchemaVersion) -> SourceSchemaOut:
    return SourceSchemaOut(id=s.id, version=s.version, fingerprint=s.fingerprint, sheet_name=s.sheet_name,
                           header_row=s.header_row, columns=s.columns, template_ref=s.template_ref,
                           template_match=s.template_match, created_at=s.created_at)


def _upload_out(db, u: EvidenceUpload, *, duplicate: bool = False) -> UploadOut:
    schema = service.schema_version(db, u)
    return UploadOut(**_summary(u, schema).model_dump(), sha256=u.sha256, scan=u.scan, discovery=u.discovery,
                     schema=_schema_out(schema) if schema else None, duplicate=duplicate)


def _overview_out(db, ov: service.EvidenceOverview) -> EvidenceOverviewOut:
    ids = [u.schema_version_id for d in ov.domains for u in d.uploads if u.schema_version_id]
    versions = {s.id: s for s in db.scalars(select(SourceSchemaVersion).where(SourceSchemaVersion.id.in_(ids)))}         if ids else {}
    return EvidenceOverviewOut(
        run_id=ov.run.id, run_code=ov.run.run_code, run_name=ov.run.name, version=ov.run.version,
        editable=ov.editable, selection_confirmed=ov.selection_confirmed,
        domains=[EvidenceDomainOut(
            key=d.domain.value, label=DOMAINS[d.domain].label, live_system=DOMAINS[d.domain].live_system,
            typical_systems=DOMAINS[d.domain].typical_systems, selected=d.selected, proposed=d.proposed,
            proposal_reasons=d.reasons, status=d.status.value, template_ref=d.template_ref,
            source_types=[SourceTypeOut(type=t.value, label=SOURCE_TYPE_LABELS[t], available=t in AVAILABLE_SOURCE_TYPES)
                          for t in SourceType],
            uploads=[_summary(u, versions.get(u.schema_version_id)) for u in d.uploads])
            for d in ov.domains])


@router.get("/evidence/domains", response_model=list[EvidenceDomainInfo], summary="Evidence domains and templates")
def list_domains(user: CurrentUser, db: DbDep) -> list[EvidenceDomainInfo]:
    out = []
    for d, info in DOMAINS.items():
        tpl = service.template_for(db, user.tenant_id, d)
        out.append(EvidenceDomainInfo(key=d.value, label=info.label, live_system=info.live_system,
                                      typical_systems=info.typical_systems,
                                      canonical_entities=list(info.canonical_entities),
                                      template_ref=tpl[0] if tpl else None))
    return out


@router.get("/evidence/domains/{domain}/template", responses={200: {"content": {XLSX: {}}}, **ERRORS},
            response_class=Response, summary="Download the governed Excel template of a domain")
def download_template(domain: str, user: CurrentUser, db: DbDep) -> Response:
    filename, data = service.template_file(db, user.tenant_id, domain)
    return Response(content=data, media_type=XLSX,
                    headers={"Content-Disposition": f'attachment; filename="{filename}"'})


@router.get("/runs/{run_id}/evidence", response_model=EvidenceOverviewOut, responses=ERRORS,
            summary="Evidence Hub overview of a run: domains, selection, uploads")
def get_overview(run_id: uuid.UUID, user: CurrentUser, db: DbDep) -> EvidenceOverviewOut:
    return _overview_out(db, service.overview(db, user.tenant_id, run_id))


@router.put("/runs/{run_id}/evidence/domains", response_model=EvidenceOverviewOut, responses=ERRORS,
            summary="Confirm the evidence domains of a run (send the run version)")
def set_domains(run_id: uuid.UUID, body: DomainSelectionIn, user: RunEditor, db: DbDep,
                platform: PlatformDep) -> EvidenceOverviewOut:
    service.set_domains(db, platform.audit, user, run_id, body.domains, body.version)
    return _overview_out(db, service.overview(db, user.tenant_id, run_id))


@router.post("/runs/{run_id}/evidence/{domain}/uploads", response_model=UploadOut,
             status_code=status.HTTP_202_ACCEPTED,
             responses={200: {"model": UploadOut, "description": "Same file already uploaded (duplicate=true)"},
                        **ERRORS},
             summary="Upload an .xlsx file; it is quarantined, scanned and read in the background")
def upload(run_id: uuid.UUID, domain: str, request: Request, response: Response, user: Uploader, db: DbDep,
           platform: PlatformDep, file: Annotated[UploadFile, File(description=".xlsx workbook")]) -> UploadOut:
    limits = _limits(request)
    data = file.file.read(limits.max_bytes + 1)  # never read more than the limit + 1 byte
    result = service.upload_file(db, platform, limits, user, run_id, domain, file.filename or "", data)
    if result.duplicate:
        response.status_code = status.HTTP_200_OK
    return _upload_out(db, result.upload, duplicate=result.duplicate)


@router.get("/runs/{run_id}/evidence/uploads/{upload_id}", response_model=UploadOut, responses=ERRORS,
            summary="One upload with its checks and discovered sheets")
def get_upload(run_id: uuid.UUID, upload_id: uuid.UUID, user: CurrentUser, db: DbDep) -> UploadOut:
    return _upload_out(db, service.get_upload(db, user.tenant_id, run_id, upload_id))


@router.post("/runs/{run_id}/evidence/uploads/{upload_id}/preview", response_model=dict, responses=ERRORS,
             summary="Read one sheet with a chosen header row (nothing is saved)")
def preview(run_id: uuid.UUID, upload_id: uuid.UUID, body: SheetChoiceIn, request: Request, user: Uploader,
            db: DbDep, platform: PlatformDep) -> dict:
    return service.preview_sheet(db, platform, _limits(request), user, run_id, upload_id, body.sheet,
                                 body.header_row)


@router.post("/runs/{run_id}/evidence/uploads/{upload_id}/confirm", response_model=UploadOut, responses=ERRORS,
             summary="Confirm sheet + header row; records an immutable source schema version")
def confirm(run_id: uuid.UUID, upload_id: uuid.UUID, body: SheetConfirmIn, request: Request, user: Uploader,
            db: DbDep, platform: PlatformDep) -> UploadOut:
    result = service.confirm_schema(db, platform, _limits(request), user, run_id, upload_id, body.sheet,
                                    body.header_row)
    return _upload_out(db, result.upload)
