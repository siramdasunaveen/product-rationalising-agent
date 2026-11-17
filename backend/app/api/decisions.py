"""Decisions (screen 09) and approvals (screen 10)."""

import uuid
from datetime import date
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.api.deps import DbDep, PlatformDep, require_permission
from app.api.evidence_requests import require_any
from app.api.schemas import ErrorResponse
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import decisions as service
from app.services.runs import get_run

router = APIRouter(tags=["decisions"])
ERRORS = {404: {"model": ErrorResponse, "description": "Run, candidate or decision not found"},
          409: {"model": ErrorResponse, "description": "conflict or submission_blocked (reasons in errors)"},
          422: {"model": ErrorResponse, "description": "validation_failed with field errors"}}
Decider = Annotated[Principal, Depends(require_permission(Permission.DECISION_SUBMIT))]
Approver = Annotated[Principal, Depends(require_permission(Permission.APPROVAL_ACT))]
Reader = Annotated[Principal, Depends(require_any(Permission.CANDIDATE_VIEW, Permission.APPROVAL_ACT))]


class DecisionIn(BaseModel):
    disposition: str = Field(description="RETAIN | SIMPLIFY | CONSOLIDATE | STANDARDISE | MIGRATE | RETIRE | INVESTIGATE")
    targetProductId: str | None = Field(default=None, max_length=64)
    effectiveDate: date | None = None
    ownerId: str | None = Field(default=None, max_length=128)
    rationale: str = Field(default="", max_length=4000)
    commercialTreatment: str | None = Field(default=None, max_length=200)


class SubmitIn(BaseModel):
    versionId: str
    contentHash: str | None = None


class ActionIn(BaseModel):
    action: str = Field(description="APPROVE | RETURN | HOLD | REJECT")
    comment: str | None = Field(default=None, max_length=2000)
    contentHash: str | None = Field(default=None, description="Hash of the version you reviewed; a mismatch is refused")


@router.get("/runs/{run_id}/candidates/{candidate_code}/decision", response_model=dict | None, responses=ERRORS,
            summary="The candidate's decision (current version, policy checks, tasks, history) or null")
def get_for_candidate(run_id: uuid.UUID, candidate_code: str, user: Reader, db: DbDep) -> dict | None:
    run = get_run(db, user.tenant_id, run_id)
    d = service.for_candidate(db, run, candidate_code.upper())
    return service.detail(db, run, d.code, user) if d else None


@router.post("/runs/{run_id}/candidates/{candidate_code}/decision", response_model=dict, responses=ERRORS,
             status_code=status.HTTP_201_CREATED, summary="Save the decision as a new immutable version (DRAFT)")
def save(run_id: uuid.UUID, candidate_code: str, body: DecisionIn, user: Decider, db: DbDep,
         platform: PlatformDep) -> dict:
    run = get_run(db, user.tenant_id, run_id)
    form = {"disposition": body.disposition.upper(), "targetProductId": body.targetProductId or None,
            "effectiveDate": body.effectiveDate, "ownerId": body.ownerId or user.user_id, "rationale": body.rationale,
            "commercialTreatment": body.commercialTreatment}
    return service.save_draft(db, platform, user, run, candidate_code.upper(), form)


@router.post("/runs/{run_id}/candidates/{candidate_code}/decision/submit", response_model=dict, responses=ERRORS,
             summary="Submit the current version for approval (gated: rules 5 and 6, execution gate)")
def submit(run_id: uuid.UUID, candidate_code: str, body: SubmitIn, user: Decider, db: DbDep,
           platform: PlatformDep) -> dict:
    run = get_run(db, user.tenant_id, run_id, for_update=True)
    return service.submit(db, platform, user, run, candidate_code.upper(), body.versionId, body.contentHash)


@router.get("/approvals", response_model=list[dict], summary="My approval queue (decisions awaiting my scope)")
def my_queue(user: Reader, db: DbDep, all_: Annotated[bool, Query(alias="all")] = False) -> list[dict]:
    return service.queue(db, user, mine=not all_)


@router.get("/runs/{run_id}/decisions", response_model=list[dict], responses=ERRORS,
            summary="Decisions of a run in approval")
def run_queue(run_id: uuid.UUID, user: Reader, db: DbDep) -> list[dict]:
    return service.queue(db, user, run=get_run(db, user.tenant_id, run_id), mine=False)


@router.get("/runs/{run_id}/decisions/{code}", response_model=dict, responses=ERRORS,
            summary="Approval view: exact decision version, policy checks (re-evaluated), tasks, history")
def get_decision(run_id: uuid.UUID, code: str, user: Reader, db: DbDep) -> dict:
    return service.detail(db, get_run(db, user.tenant_id, run_id), code, user)


@router.post("/runs/{run_id}/decisions/{code}/actions", response_model=dict, responses=ERRORS,
             summary="Approve, return, hold or reject the exact version (comment required except for approve)")
def act(run_id: uuid.UUID, code: str, body: ActionIn, user: Approver, db: DbDep, platform: PlatformDep) -> dict:
    run = get_run(db, user.tenant_id, run_id, for_update=True)
    return service.act(db, platform, user, run, code, body.action, body.comment, body.contentHash)
