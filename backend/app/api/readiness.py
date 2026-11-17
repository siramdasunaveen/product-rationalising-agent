"""Evidence readiness (step 7, screen 11)."""

import uuid
from typing import Annotated, Literal

from fastapi import APIRouter, Query
from pydantic import BaseModel, Field

from app.api.deps import CurrentUser, DbDep
from app.api.schemas import ErrorResponse
from app.services import readiness as service

router = APIRouter(tags=["evidence-readiness"])


class ReadinessKpis(BaseModel):
    overall_completeness: int = Field(description="Weighted average coverage of the selected domains (0-100)")
    domains_available: int
    domains_selected: int
    assessment_limitations: int = Field(description="Assessments that are LIMITED, NOT_ASSESSED or BLOCKED")
    approval_blockers: int = Field(description="Domains needed by a disposition that are not AVAILABLE")
    products: int


class DomainReadiness(BaseModel):
    domain: str
    label: str
    selected: bool
    importance: str = Field(description="CRITICAL | HIGH | MEDIUM | LOW")
    importance_label: str
    status: str = Field(description="AVAILABLE | PARTIAL | NOT_ASSESSED | BLOCKED")
    status_label: str = Field(description="NOT_ASSESSED is shown as Missing")
    coverage: int = Field(description="Percent of the run's products covered")
    covered: int
    total: int
    stale: bool
    conflicted_facts: int
    consequence: str
    action: str


class AssessmentReadiness(BaseModel):
    key: str
    label: str
    status: str = Field(description="AVAILABLE | LIMITED | NOT_ASSESSED | BLOCKED")
    limited_by: list[str]


class MissingDomain(BaseModel):
    domain: str
    status: str


class DispositionReadiness(BaseModel):
    disposition: str
    requires: list[str]
    ready: bool
    missing: list[MissingDomain]


class ReadinessOut(BaseModel):
    run_id: uuid.UUID
    source: str = Field(description="snapshot | preview")
    based_on: dict = Field(description="kind snapshot (code, content_hash) | preview | none")
    policy_ref: str
    kpis: ReadinessKpis
    domains: list[DomainReadiness]
    assessments: list[AssessmentReadiness]
    dispositions: list[DispositionReadiness]
    result_hash: str


@router.get("/runs/{run_id}/evidence-readiness", response_model=ReadinessOut,
            responses={404: {"model": ErrorResponse, "description": "Run not found"},
                       409: {"model": ErrorResponse, "description": "No published readiness policy"}},
            summary="Evidence completeness, consequences, assessments and dispositions of a run")
def evidence_readiness(run_id: uuid.UUID, user: CurrentUser, db: DbDep,
                       source: Annotated[Literal["snapshot", "preview"], Query()] = "snapshot") -> ReadinessOut:
    r = service.evidence_readiness(db, user.tenant_id, run_id, source)
    return ReadinessOut(run_id=r.run_id, source=r.source, based_on=r.based_on, policy_ref=r.policy_ref, **r.result)
