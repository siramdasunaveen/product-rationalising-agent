"""Candidate Explorer (screen 07) and Candidate Review (screens 08/12)."""

import uuid
from datetime import datetime
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Query, status
from pydantic import BaseModel, Field

from app.api.deps import DbDep, PlatformDep, require_permission
from app.api.schemas import ErrorResponse
from app.domain import candidate_views as cv
from app.domain.permissions import Permission
from app.ports.auth import Principal
from app.services import candidates as service
from app.services.runs import get_run

router = APIRouter(tags=["candidates"])
Viewer = Annotated[Principal, Depends(require_permission(Permission.CANDIDATE_VIEW))]
ERRORS = {404: {"model": ErrorResponse, "description": "Run, candidate or saved view not found"},
          409: {"model": ErrorResponse, "description": "stale: the candidate was reassessed; refresh"},
          422: {"model": ErrorResponse, "description": "validation_failed with field errors"}}


def _csv(value: str | None) -> list[str] | None:
    if value is None or not value.strip():
        return None
    return [v.strip().upper() for v in value.split(",") if v.strip()]


class CandidateItem(BaseModel):
    id: uuid.UUID
    candidateId: str = Field(description="Stable candidate code, e.g. CAND-0001")
    products: list[str]
    productIds: list[str]
    primaryType: str
    primaryTypeLabel: str
    supportingTypes: list[str]
    score: int | None = Field(description="Opportunity Score 0-100 (never priority)")
    effectivePriority: str | None = Field(description="LOW | MEDIUM | HIGH | CRITICAL after policy overrides")
    recommendation: str | None
    impactSummary: str
    status: str = Field(description="REVIEW | BLOCKED | EVIDENCE_GAP | DECISION | APPROVAL | APPROVED")
    hidden: bool = Field(description="Below review and hidden from the default view (never deleted)")
    decisionReadiness: str | None = Field(description="PASS | BLOCKED for the recommended action")
    similarity: int | None = Field(description="Primary detector similarity, per mille")


class CandidateList(BaseModel):
    runId: uuid.UUID
    runCode: str
    snapshot: dict | None
    inventoryTotal: int = Field(description="All active candidates of the run, whatever the view")
    filteredCount: int = Field(description="Candidates matching the effective filters")
    savedView: dict | None
    effectiveFilters: dict = Field(description="Exactly the filters the server applied; draw the filter bar from it")
    sort: list[dict]
    items: list[CandidateItem]
    page: int
    pageSize: int
    projectionVersion: int
    analysed: bool


class SavedViewIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    filters: dict[str, Any] = Field(default_factory=dict)
    sort: list[dict[str, str]] | None = None
    visibleColumns: list[str] | None = None


class ReadinessIn(BaseModel):
    action: str = Field(description="RETAIN | SIMPLIFY | CONSOLIDATE | STANDARDISE | MIGRATE | RETIRE | INVESTIGATE")
    targetProductId: str | None = Field(default=None, max_length=64)
    analysisId: str | None = Field(default=None, description="analysisId you loaded; a mismatch fails closed")


class CommentIn(BaseModel):
    body: str = Field(min_length=1, max_length=service.COMMENT_MAX)


class CommentOut(BaseModel):
    id: uuid.UUID
    authorId: str
    body: str
    createdAt: datetime


class ExplanationOut(BaseModel):
    source: str = Field(description="llm | fallback (deterministic template)")
    sentences: list[dict]
    aiConfidence: float | None = Field(description="Only for an accepted LLM explanation; never used in any score")
    belowThreshold: bool
    model: str | None
    createdAt: datetime


@router.get("/runs/{run_id}/candidates", response_model=CandidateList, responses=ERRORS,
            summary="Candidate Explorer: saved views and filters over the run's active candidates")
def list_candidates(
    run_id: uuid.UUID, user: Viewer, db: DbDep,
    view_id: Annotated[str | None, Query(alias="viewId", max_length=64)] = None,
    query: Annotated[str | None, Query(max_length=cv.MAX_QUERY)] = None,
    types: Annotated[str | None, Query(max_length=200, description="Comma-separated; primary or supporting")] = None,
    priority: Annotated[str | None, Query(max_length=100)] = None,
    status_: Annotated[str | None, Query(alias="status", max_length=200)] = None,
    recommendation: Annotated[str | None, Query(max_length=200)] = None,
    decision_readiness: Annotated[str | None, Query(alias="decisionReadiness", max_length=40)] = None,
    min_score: Annotated[int | None, Query(alias="minScore", ge=0, le=100)] = None,
    include_hidden: Annotated[bool | None, Query(alias="includeHidden")] = None,
    sort: Annotated[str | None, Query(max_length=200, description="e.g. priority:DESC,score:DESC")] = None,
    page: Annotated[int, Query(ge=1)] = 1,
    page_size: Annotated[int, Query(alias="pageSize", ge=1, le=service.MAX_PAGE_SIZE)] = 50,
) -> CandidateList:
    run = get_run(db, user.tenant_id, run_id)
    explicit = {"types": _csv(types), "priority": _csv(priority), "status": _csv(status_),
                "recommendation": _csv(recommendation), "decisionReadiness": _csv(decision_readiness),
                "minScore": min_score, "query": query.strip() if query and query.strip() else None,
                "includeHidden": include_hidden}
    sort_spec = None
    if sort:
        sort_spec = []
        for part in sort.split(","):
            fld, _, direction = part.partition(":")
            sort_spec.append({"field": fld.strip(), "direction": (direction or "ASC").strip().upper()})
    return CandidateList(**service.list_candidates(db, run, user, view_id=view_id, explicit=explicit,
                                                   sort=sort_spec, page=page, page_size=page_size))


@router.get("/candidate-views", response_model=list[dict], summary="Built-in team views and your personal views")
def views(user: Viewer, db: DbDep) -> list[dict]:
    return service.list_views(db, user)


@router.post("/candidate-views", response_model=dict, status_code=status.HTTP_201_CREATED, responses=ERRORS,
             summary="Save a personal view (configuration only)")
def save_view(body: SavedViewIn, user: Viewer, db: DbDep) -> dict:
    return service.save_view(db, user, body.name, body.filters, body.sort, body.visibleColumns)


@router.delete("/candidate-views/{view_id}", status_code=status.HTTP_204_NO_CONTENT, responses=ERRORS,
               summary="Delete one of your personal views")
def delete_view(view_id: str, user: Viewer, db: DbDep) -> None:
    service.delete_view(db, user, view_id)


@router.get("/runs/{run_id}/candidates/{candidate_ref}", response_model=dict, responses=ERRORS,
            summary="Candidate Review: metrics, dimensions, evidence, impact, alternatives, policies, readiness")
def get_candidate(run_id: uuid.UUID, candidate_ref: str, user: Viewer, db: DbDep) -> dict:
    return service.detail(db, get_run(db, user.tenant_id, run_id), candidate_ref)


@router.post("/runs/{run_id}/candidates/{candidate_ref}/decision-readiness", response_model=dict, responses=ERRORS,
             summary="Server-side pre-check for Continue to Decision (fails closed when stale)")
def decision_readiness(run_id: uuid.UUID, candidate_ref: str, body: ReadinessIn, user: Viewer, db: DbDep) -> dict:
    return service.check_readiness(db, get_run(db, user.tenant_id, run_id), candidate_ref, body.action.upper(),
                                   body.targetProductId, body.analysisId)


@router.get("/runs/{run_id}/candidates/{candidate_ref}/comments", response_model=list[CommentOut], responses=ERRORS,
            summary="Discussion (collaboration only, never evidence)")
def list_comments(run_id: uuid.UUID, candidate_ref: str, user: Viewer, db: DbDep) -> list[CommentOut]:
    rows = service.comments(db, get_run(db, user.tenant_id, run_id), candidate_ref)
    return [CommentOut(id=r.id, authorId=r.author_id, body=r.body, createdAt=r.created_at) for r in rows]


@router.post("/runs/{run_id}/candidates/{candidate_ref}/comments", response_model=CommentOut,
             status_code=status.HTTP_201_CREATED, responses=ERRORS, summary="Add a comment (audited)")
def add_comment(run_id: uuid.UUID, candidate_ref: str, body: CommentIn, user: Viewer, db: DbDep,
                platform: PlatformDep) -> CommentOut:
    r = service.add_comment(db, platform, user, get_run(db, user.tenant_id, run_id), candidate_ref, body.body)
    return CommentOut(id=r.id, authorId=r.author_id, body=r.body, createdAt=r.created_at)


@router.post("/runs/{run_id}/candidates/{candidate_ref}/explanation", response_model=ExplanationOut,
             status_code=status.HTTP_201_CREATED, responses=ERRORS,
             summary="Optional AI explanation: cited, validated, deterministic template as fallback")
def explain(run_id: uuid.UUID, candidate_ref: str, user: Viewer, db: DbDep, platform: PlatformDep) -> ExplanationOut:
    r = service.explain(db, platform, user, get_run(db, user.tenant_id, run_id), candidate_ref)
    return ExplanationOut(source=r.source, sentences=r.sentences,
                          aiConfidence=None if r.confidence is None else r.confidence / 1000,
                          belowThreshold=r.below_threshold, model=r.model, createdAt=r.created_at)
