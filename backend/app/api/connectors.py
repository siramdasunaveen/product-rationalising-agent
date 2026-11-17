"""Live connectors (step 15): approved source systems and pulls into a run's evidence."""

import uuid
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Request, status
from pydantic import BaseModel, Field

from app.api.deps import DbDep, PlatformDep, require_permission
from app.api.evidence import _limits, _upload_out
from app.api.evidence_requests import require_any
from app.api.schemas import ErrorResponse
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import connectors as service
from app.services.runs import get_run

router = APIRouter(tags=["connectors"])
ERRORS = {404: {"model": ErrorResponse, "description": "Connector or run not found"},
          409: {"model": ErrorResponse, "description": "conflict (credentials missing, connector disabled)"},
          422: {"model": ErrorResponse, "description": "validation_failed"},
          502: {"model": ErrorResponse, "description": "connector_unavailable: the source system could not be read"}}
Admin = Annotated[Principal, Depends(require_permission(Permission.CONNECTOR_MANAGE))]
Reader = Annotated[Principal, Depends(require_any(Permission.CONNECTOR_MANAGE, Permission.EVIDENCE_UPLOAD,
                                                  Permission.EVIDENCE_MAP))]
Puller = Annotated[Principal, Depends(require_permission(Permission.EVIDENCE_UPLOAD))]


class ConnectorIn(BaseModel):
    key: str = Field(max_length=40, description="Stable id, e.g. sf-prod")
    name: str = Field(max_length=120)
    system: str = Field(description="SALESFORCE")
    instanceUrl: str = Field(max_length=255)
    apiVersion: str | None = Field(default=None, max_length=8)
    authType: str | None = Field(default=None, description="OAUTH_CLIENT_CREDENTIALS")
    clientId: str | None = Field(default=None, max_length=255)
    clientSecret: str | None = Field(default=None, max_length=4000, description="Write-only; stored encrypted")
    queries: dict[str, Any] | None = Field(default=None, description="Per-domain object and column overrides")
    status: str | None = Field(default=None, description="ACTIVE | DISABLED")


class PullIn(BaseModel):
    connectorId: uuid.UUID


@router.get("/connectors", response_model=list[dict], summary="Approved live connectors (secrets never returned)")
def list_connectors(user: Reader, db: DbDep) -> list[dict]:
    return [service.to_dict(c) for c in service.list_connectors(db, user.tenant_id)]


@router.post("/connectors", response_model=dict, status_code=status.HTTP_201_CREATED, responses=ERRORS,
             summary="Register a connector (Agent Admin)")
def create(body: ConnectorIn, user: Admin, db: DbDep, platform: PlatformDep) -> dict:
    return service.to_dict(service.save(db, platform, user, body.model_dump()))


@router.put("/connectors/{connector_id}", response_model=dict, responses=ERRORS, summary="Update a connector")
def update(connector_id: uuid.UUID, body: ConnectorIn, user: Admin, db: DbDep, platform: PlatformDep) -> dict:
    return service.to_dict(service.save(db, platform, user, body.model_dump(), connector_id))


@router.delete("/connectors/{connector_id}", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS,
               summary="Delete a connector (disabled instead when pulls reference it)")
def remove(connector_id: uuid.UUID, user: Admin, db: DbDep, platform: PlatformDep) -> None:
    service.delete(db, platform, user, connector_id)


@router.post("/connectors/{connector_id}/test", response_model=dict, responses=ERRORS,
             summary="Test the connection with the stored credentials")
def test(connector_id: uuid.UUID, user: Admin, db: DbDep, platform: PlatformDep) -> dict:
    return service.test(db, platform, user, connector_id)


@router.post("/runs/{run_id}/evidence/{domain}/pull", response_model=dict, status_code=status.HTTP_201_CREATED,
             responses=ERRORS, summary="Pull the domain from a connector into the run (checked and discovered)")
def pull(run_id: uuid.UUID, domain: str, body: PullIn, request: Request, user: Puller, db: DbDep,
         platform: PlatformDep) -> dict:
    get_run(db, user.tenant_id, run_id)
    record, upload = service.pull(db, platform, _limits(request), user, run_id, domain, body.connectorId)
    return {"pullId": str(record.id), "rows": record.rows, "soql": record.soql,
            "upload": _upload_out(db, upload).model_dump(mode="json", by_alias=True)}


@router.get("/runs/{run_id}/evidence/pulls", response_model=list[dict], responses=ERRORS,
            summary="Connector pulls of the run (lineage)")
def list_pulls(run_id: uuid.UUID, user: Reader, db: DbDep) -> list[dict]:
    run = get_run(db, user.tenant_id, run_id)
    return [{"id": str(p.id), "connectorId": str(p.connector_id), "domain": p.domain, "status": p.status,
             "rows": p.rows, "uploadId": str(p.upload_id) if p.upload_id else None, "error": p.error,
             "requestedBy": p.requested_by, "at": p.created_at.isoformat()} for p in service.pulls(db, run)]
