"""Reference data for pickers: portfolios, markets, product families, objectives and people."""

import uuid
from typing import Annotated

from fastapi import APIRouter, Query

from app.api.deps import CurrentUser, DbDep, PlatformDep
from app.api.schemas import MarketOut, ObjectiveOut, PersonOut, PortfolioOut, ProductFamilyOut
from app.domain.permissions import PERSONAS
from app.domain.runs import OBJECTIVE_LABELS
from app.services import reference

router = APIRouter(tags=["metadata"])


@router.get("/metadata/portfolios", response_model=list[PortfolioOut], summary="Portfolios the user may scope")
def portfolios(user: CurrentUser, db: DbDep) -> list[PortfolioOut]:
    return [PortfolioOut(id=p.id, code=p.code, name=p.name)
            for p in reference.list_portfolios(db, user.tenant_id)]


@router.get("/metadata/markets", response_model=list[MarketOut], summary="Markets / operating companies")
def markets(user: CurrentUser, db: DbDep) -> list[MarketOut]:
    return [MarketOut(id=m.id, code=m.code, name=m.name, timezone=m.timezone)
            for m in reference.list_markets(db, user.tenant_id)]


@router.get("/metadata/product-families", response_model=list[ProductFamilyOut],
            summary="Product family tree (flat list with parent_id)")
def product_families(user: CurrentUser, db: DbDep,
                     portfolio_id: uuid.UUID | None = None) -> list[ProductFamilyOut]:
    return [ProductFamilyOut(id=f.id, code=f.code, name=f.name, portfolio_id=f.portfolio_id,
                             parent_id=f.parent_id)
            for f in reference.list_product_families(db, user.tenant_id, portfolio_id)]


@router.get("/metadata/objectives", response_model=list[ObjectiveOut], summary="Business objectives")
def objectives(_: CurrentUser) -> list[ObjectiveOut]:
    return [ObjectiveOut(key=k.value, label=label) for k, label in OBJECTIVE_LABELS.items()]


@router.get("/people", response_model=list[PersonOut], summary="People picker (filter by role)")
def people(user: CurrentUser, db: DbDep, platform: PlatformDep,
           role: Annotated[list[str] | None, Query()] = None,
           query: Annotated[str, Query(max_length=100)] = "") -> list[PersonOut]:
    roles = tuple(r for r in (role or []) if r in PERSONAS)
    found = platform.people.search(user.tenant_id, roles=roles, query=query, session=db)
    return [PersonOut(user_id=p.user_id, name=p.name, email=p.email, roles=list(p.roles)) for p in found]
