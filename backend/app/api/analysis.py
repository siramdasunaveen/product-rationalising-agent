"""Analysis workflow status, detector findings and candidates (steps 10-12)."""

import uuid
from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.api.deps import CurrentUser, DbDep, PlatformDep, require_permission
from app.api.schemas import ErrorResponse
from app.db.models import Candidate, DetectorFinding
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import analysis as service
from app.services.runs import get_run

router = APIRouter(tags=["analysis"])
Starter = Annotated[Principal, Depends(require_permission(Permission.RUN_START))]
Viewer = Annotated[Principal, Depends(require_permission(Permission.CANDIDATE_VIEW))]
ERRORS = {404: {"model": ErrorResponse, "description": "Run or analysis not found"},
          409: {"model": ErrorResponse, "description": "conflict"}}


class AnalysisOut(BaseModel):
    id: uuid.UUID
    seq: int
    reason: str
    status: str = Field(description="QUEUED | RUNNING | SUCCEEDED | FAILED")
    stages: list[dict] = Field(description="Discover, Normalize, Analyze, Assess, Recommend, Review, Approve")
    source_products: dict | None
    counts: dict | None = Field(description="rawFindings, merged, invalid, outOfScope, superseded, candidates")
    detector_summary: dict | None
    result_hash: str | None
    error: str | None
    created_at: datetime
    finished_at: datetime | None


class FindingOut(BaseModel):
    id: uuid.UUID
    finding_key: str
    detector_type: str
    detector_version: str
    subjects: list[str]
    signal: int = Field(description="Per mille (900 = 0.90)")
    threshold: int
    band: str
    factors: dict
    coverage: int
    evidence_ids: list[str]
    notes: list[str]
    status: str
    candidate_id: uuid.UUID | None


def _analysis(a) -> AnalysisOut:
    sp = dict(a.source_products or {}) or None
    if sp:
        sp.pop("products", None)
    return AnalysisOut(id=a.id, seq=a.seq, reason=a.reason, status=a.status, stages=a.stages, source_products=sp,
                       counts=a.counts, detector_summary=a.detector_summary, result_hash=a.result_hash,
                       error=a.error, created_at=a.created_at, finished_at=a.finished_at)


@router.get("/runs/{run_id}/analysis", response_model=AnalysisOut | None, responses=ERRORS,
            summary="Latest analysis of the run: stage progress, source products, counts")
def get_analysis(run_id: uuid.UUID, user: CurrentUser, db: DbDep) -> AnalysisOut | None:
    a = service.latest(db, get_run(db, user.tenant_id, run_id))
    return _analysis(a) if a else None


@router.post("/runs/{run_id}/analysis/retry", response_model=AnalysisOut, status_code=status.HTTP_202_ACCEPTED,
             responses=ERRORS, summary="Retry a failed analysis with the same pinned configuration")
def retry(run_id: uuid.UUID, user: Starter, db: DbDep, platform: PlatformDep) -> AnalysisOut:
    return _analysis(service.retry(db, platform, user, run_id))


@router.get("/runs/{run_id}/findings", response_model=list[FindingOut], responses=ERRORS,
            summary="Detector findings of the latest successful analysis")
def findings(run_id: uuid.UUID, user: Viewer, db: DbDep,
             detector_type: Annotated[str | None, Query(alias="type", max_length=32)] = None) -> list[FindingOut]:
    run = get_run(db, user.tenant_id, run_id)
    a = service.latest(db, run, succeeded=True)
    if a is None:
        return []
    stmt = select(DetectorFinding).where(DetectorFinding.analysis_id == a.id, DetectorFinding.tenant_id == run.tenant_id)
    if detector_type:
        stmt = stmt.where(DetectorFinding.detector_type == detector_type)
    return [FindingOut.model_validate(f, from_attributes=True)
            for f in db.scalars(stmt.order_by(DetectorFinding.detector_type, DetectorFinding.finding_key))]


def latest_candidates(db, run, *, include_inactive: bool = False) -> list[Candidate]:
    a = service.latest(db, run, succeeded=True)
    if a is None:
        return []
    stmt = select(Candidate).where(Candidate.run_id == run.id, Candidate.tenant_id == run.tenant_id)
    stmt = stmt.where(Candidate.analysis_id == a.id) if not include_inactive else stmt
    return list(db.scalars(stmt.order_by(Candidate.code)))
