"""Runs (assessments): screens 01 Portfolio Runs and 02 Define Scope."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Depends, Query, status

from app.api.deps import CurrentUser, DbDep, PlatformDep, require_permission
from app.api.schemas import (
    ErrorResponse,
    OwnerRef,
    Ref,
    RunListItem,
    RunListOut,
    RunOut,
    RunScopeIn,
    RunScopePatch,
    RunSummaryOut,
)
from app.db.models import Market, Portfolio, ProductFamily, Run
from app.domain.permissions import Permission
from app.domain.runs import SCOPE_EDITABLE_STATES, STATE_LABELS, RunState, ScopeInput
from app.ports.auth import Principal
from app.services import runs as service
from app.services.reference import names_by_id

router = APIRouter(prefix="/runs", tags=["runs"])

Editor = Annotated[Principal, Depends(require_permission(Permission.RUN_CREATE))]

ERRORS = {
    404: {"model": ErrorResponse, "description": "Run not found in your tenant"},
    409: {"model": ErrorResponse, "description": "name_taken, version_conflict or not_editable"},
    422: {"model": ErrorResponse, "description": "validation_failed with field errors"},
}


def _label(state: str) -> str:
    try:
        return STATE_LABELS[RunState(state)]
    except ValueError:
        return state


def _run_out(db, run: Run) -> RunOut:
    portfolio = names_by_id(db, Portfolio, run.tenant_id, [run.portfolio_id])
    markets = names_by_id(db, Market, run.tenant_id, run.market_ids)
    families = names_by_id(db, ProductFamily, run.tenant_id, run.product_family_ids)
    return RunOut(
        id=run.id, run_code=run.run_code, name=run.name, state=run.state, stage_label=_label(run.state),
        portfolio=Ref(id=run.portfolio_id, name=portfolio.get(run.portfolio_id, "?")),
        markets=[Ref(id=m, name=markets.get(m, "?")) for m in run.market_ids],
        product_families=[Ref(id=f, name=families.get(f, "?")) for f in run.product_family_ids],
        period_start=run.period_start, period_end=run.period_end, objectives=list(run.objectives),
        owner=OwnerRef(user_id=run.owner_id, name=run.owner_name), risk=None,
        scope_editable=run.state in SCOPE_EDITABLE_STATES, version=run.version,
        created_at=run.created_at, updated_at=run.updated_at,
    )


@router.get("", response_model=RunListOut, summary="List and search runs")
def list_runs(user: CurrentUser, db: DbDep,
              query: Annotated[str, Query(max_length=100, description="Run, portfolio or owner")] = "",
              limit: Annotated[int, Query(ge=1, le=200)] = 50,
              offset: Annotated[int, Query(ge=0)] = 0) -> RunListOut:
    rows, total = service.list_runs(db, user.tenant_id, query=query, limit=limit, offset=offset)
    return RunListOut(total=total, items=[
        RunListItem(id=r.id, run_code=r.run_code, name=r.name, state=r.state, stage_label=_label(r.state),
                    portfolio_name=pname, owner_name=r.owner_name, risk=None, updated_at=r.updated_at)
        for r, pname in rows
    ])


@router.get("/summary", response_model=RunSummaryOut, summary="KPI tiles for the Assessments home")
def summary(user: CurrentUser, db: DbDep) -> RunSummaryOut:
    s = service.summarise(db, user.tenant_id)
    return RunSummaryOut(active_runs=s.active_runs, opportunities=s.opportunities,
                         awaiting_approval=s.awaiting_approval, blocked=s.blocked)


@router.post("", response_model=RunOut, status_code=status.HTTP_201_CREATED, responses=ERRORS,
             summary="Create a run with its scope (state CONFIGURING)")
def create_run(body: RunScopeIn, user: Editor, db: DbDep, platform: PlatformDep) -> RunOut:
    run = service.create_run(db, platform.audit, platform.people, user, ScopeInput(**body.model_dump()))
    return _run_out(db, run)


@router.get("/{run_id}", response_model=RunOut, responses=ERRORS, summary="Get a run (reopen)")
def get_run(run_id: uuid.UUID, user: CurrentUser, db: DbDep) -> RunOut:
    return _run_out(db, service.get_run(db, user.tenant_id, run_id))


@router.patch("/{run_id}", response_model=RunOut, responses=ERRORS,
              summary="Change the scope (only while CONFIGURING; send the version you loaded)")
def update_run(run_id: uuid.UUID, body: RunScopePatch, user: Editor, db: DbDep,
               platform: PlatformDep) -> RunOut:
    changes = body.model_dump(exclude_unset=True)
    version = changes.pop("version")
    run = service.update_run_scope(db, platform.audit, platform.people, user, run_id, changes, version)
    return _run_out(db, run)
