"""Evidence gaps and requests (screens 13, 14, 15): the role-aware queue, the logical evidence contract and the
lifecycle DRAFT -> OPEN -> IN_PROGRESS -> RECEIVED -> ACCEPTED | REJECTED | SUPERSEDED."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from pydantic import BaseModel, Field

from app.api.deps import DbDep, PlatformDep, get_principal
from app.api.schemas import ErrorResponse
from app.domain import evidence_requests as er
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import evidence_requests as service
from app.services.runs import get_run

router = APIRouter(tags=["evidence-requests"])
ERRORS = {404: {"model": ErrorResponse, "description": "Run or request not found"},
          409: {"model": ErrorResponse, "description": "The request is not in a state that allows this"},
          422: {"model": ErrorResponse, "description": "validation_failed with field errors"}}


def require_any(*permissions: Permission):
    def dependency(principal: Annotated[Principal, Depends(get_principal)]) -> Principal:
        if not any(principal.has(p.value) for p in permissions):
            raise HTTPException(status.HTTP_403_FORBIDDEN,
                                "Missing permission: " + " or ".join(p.value for p in permissions))
        return principal
    return dependency


Reader = Annotated[Principal, Depends(require_any(Permission.CANDIDATE_VIEW, Permission.REQUEST_FULFIL,
                                                  Permission.EVIDENCE_MAP, Permission.SNAPSHOT_APPROVE))]
Requester = Annotated[Principal, Depends(require_any(Permission.DECISION_SUBMIT, Permission.EVIDENCE_MAP))]
Fulfiller = Annotated[Principal, Depends(require_any(Permission.REQUEST_FULFIL, Permission.EVIDENCE_UPLOAD,
                                                     Permission.EVIDENCE_MAP))]
Acceptor = Annotated[Principal, Depends(require_any(Permission.SNAPSHOT_APPROVE))]


class QueueOut(BaseModel):
    metrics: dict = Field(description="open, openCandidates, blocking, awaiting, ready, reassessing")
    items: list[dict]


class AssignIn(BaseModel):
    ownerId: str | None = Field(default=None, max_length=128)
    dueDate: date | None = None
    fulfilmentMethod: str | None = Field(default=None, description="SECURE_QUERY | API_CONNECTOR | SCHEDULED_EXTRACT | EXCEL_AGGREGATE")


class RejectIn(BaseModel):
    reason: str = Field(min_length=1, max_length=2000)


def _csv(value: str | None) -> list[str] | None:
    return [v.strip().upper() for v in value.split(",") if v.strip()] if value else None


@router.get("/evidence-requests", response_model=QueueOut, summary="Evidence request queue across the tenant's runs")
def queue(user: Reader, db: DbDep,
          status_: Annotated[str | None, Query(alias="status", max_length=120)] = None,
          domain: Annotated[str | None, Query(max_length=40)] = None,
          mine: Annotated[bool, Query(description="Only requests assigned to me")] = False,
          query: Annotated[str | None, Query(max_length=100)] = None) -> QueueOut:
    rows = service.list_requests(db, user.tenant_id, None, status=_csv(status_), domain=domain,
                                 owner_id=user.user_id if mine else None, query=query)
    items = [service.to_dict(db, r) for r in rows]
    return QueueOut(metrics=er.queue_metrics(items), items=items)


@router.get("/runs/{run_id}/evidence-requests", response_model=QueueOut, responses=ERRORS,
            summary="Evidence request queue of one run")
def run_queue(run_id: uuid.UUID, user: Reader, db: DbDep,
              status_: Annotated[str | None, Query(alias="status", max_length=120)] = None,
              domain: Annotated[str | None, Query(max_length=40)] = None,
              candidate: Annotated[str | None, Query(max_length=16)] = None,
              mine: Annotated[bool, Query()] = False,
              query: Annotated[str | None, Query(max_length=100)] = None) -> QueueOut:
    run = get_run(db, user.tenant_id, run_id)
    rows = service.list_requests(db, user.tenant_id, run, status=_csv(status_), domain=domain, candidate=candidate,
                                 owner_id=user.user_id if mine else None, query=query)
    items = [service.to_dict(db, r) for r in rows]
    return QueueOut(metrics=er.queue_metrics(items), items=items)


@router.post("/runs/{run_id}/evidence-requests/detect", response_model=list[dict], responses=ERRORS,
             summary="Create a DRAFT request for every evidence gap of the run that has none yet")
def detect(run_id: uuid.UUID, user: Requester, db: DbDep, platform: PlatformDep) -> list[dict]:
    run = get_run(db, user.tenant_id, run_id)
    return [service.to_dict(db, r) for r in service.detect(db, platform, user, run)]


@router.post("/runs/{run_id}/candidates/{candidate_code}/evidence-requests", response_model=list[dict],
             responses=ERRORS, summary="Request evidence for one candidate (creates or opens the existing requests)")
def request_for_candidate(run_id: uuid.UUID, candidate_code: str, user: Requester, db: DbDep,
                          platform: PlatformDep) -> list[dict]:
    run = get_run(db, user.tenant_id, run_id)
    return [service.to_dict(db, r) for r in service.detect(db, platform, user, run, candidate_code.upper())]


@router.get("/runs/{run_id}/evidence-requests/{code}", response_model=dict, responses=ERRORS,
            summary="Evidence request detail: logical contract, fulfilment, history")
def detail(run_id: uuid.UUID, code: str, user: Reader, db: DbDep) -> dict:
    run = get_run(db, user.tenant_id, run_id)
    return service.to_dict(db, service.get(db, run, code), with_history=True)


@router.post("/runs/{run_id}/evidence-requests/{code}/assign", response_model=dict, responses=ERRORS,
             summary="Assign owner, due date and fulfilment channel (DRAFT -> OPEN)")
def assign(run_id: uuid.UUID, code: str, body: AssignIn, user: Requester, db: DbDep, platform: PlatformDep) -> dict:
    run = get_run(db, user.tenant_id, run_id)
    req = service.assign(db, platform, user, run, code, owner_id=body.ownerId, due_date=body.dueDate,
                         fulfilment_method=body.fulfilmentMethod.upper() if body.fulfilmentMethod else None)
    return service.to_dict(db, req, with_history=True)


@router.post("/runs/{run_id}/evidence-requests/{code}/start", response_model=dict, responses=ERRORS,
             summary="Start fulfilment through the chosen channel (OPEN -> IN_PROGRESS)")
def start(run_id: uuid.UUID, code: str, user: Fulfiller, db: DbDep, platform: PlatformDep) -> dict:
    run = get_run(db, user.tenant_id, run_id)
    return service.to_dict(db, service.start(db, platform, user, run, code), with_history=True)


@router.post("/runs/{run_id}/evidence-requests/{code}/accept", response_model=dict, responses=ERRORS,
             summary="Accept the received return: every request it satisfies is accepted and a reassessment queued")
def accept(run_id: uuid.UUID, code: str, user: Acceptor, db: DbDep, platform: PlatformDep) -> dict:
    run = get_run(db, user.tenant_id, run_id, for_update=True)
    return service.accept(db, platform, user, run, code)


@router.post("/runs/{run_id}/evidence-requests/{code}/reject", response_model=dict, responses=ERRORS,
             summary="Reject the received return (reason required); a corrected return can be received later")
def reject(run_id: uuid.UUID, code: str, body: RejectIn, user: Acceptor, db: DbDep, platform: PlatformDep) -> dict:
    run = get_run(db, user.tenant_id, run_id)
    return service.to_dict(db, service.reject(db, platform, user, run, code, body.reason), with_history=True)
