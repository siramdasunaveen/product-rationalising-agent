"""Reference data: portfolios, markets and product families (tenant-scoped)."""

import uuid
from collections.abc import Iterable

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import Market, Portfolio, ProductFamily


def list_portfolios(session: Session, tenant_id: str) -> list[Portfolio]:
    return list(session.scalars(
        select(Portfolio).where(Portfolio.tenant_id == tenant_id, Portfolio.is_active.is_(True))
        .order_by(Portfolio.name)))


def list_markets(session: Session, tenant_id: str) -> list[Market]:
    return list(session.scalars(
        select(Market).where(Market.tenant_id == tenant_id, Market.is_active.is_(True))
        .order_by(Market.name)))


def list_product_families(session: Session, tenant_id: str,
                          portfolio_id: uuid.UUID | None = None) -> list[ProductFamily]:
    stmt = select(ProductFamily).where(ProductFamily.tenant_id == tenant_id,
                                       ProductFamily.is_active.is_(True))
    if portfolio_id:
        stmt = stmt.where(ProductFamily.portfolio_id == portfolio_id)
    return list(session.scalars(stmt.order_by(ProductFamily.name)))


def names_by_id(session: Session, model, tenant_id: str, ids: Iterable[uuid.UUID]) -> dict[uuid.UUID, str]:
    ids = list(set(ids))
    if not ids:
        return {}
    rows = session.execute(select(model.id, model.name).where(model.tenant_id == tenant_id,
                                                              model.id.in_(ids)))
    return {r.id: r.name for r in rows}


# ---------------------------------------------------------------------------
# Demo seed (idempotent; keyed by code)
# ---------------------------------------------------------------------------

DEMO_PORTFOLIOS = [
    ("CONSUMER_MOBILE", "Consumer Mobile"),
    ("ENTERPRISE_CONNECTIVITY", "Enterprise Connectivity"),
    ("LEGACY_VOICE", "PSTN & Legacy Voice"),
]

DEMO_MARKETS = [
    ("UK_CONSUMER", "UK Consumer", "Europe/London"),
    ("UK_ENTERPRISE", "UK Enterprise", "Europe/London"),
    ("IE_CONSUMER", "Ireland Consumer", "Europe/Dublin"),
]

# (code, name, portfolio code, parent code)
DEMO_FAMILIES = [
    ("POSTPAID", "Postpaid", "CONSUMER_MOBILE", None),
    ("POSTPAID_SIM_ONLY", "SIM-only", "CONSUMER_MOBILE", "POSTPAID"),
    ("POSTPAID_HANDSET", "Handset plans", "CONSUMER_MOBILE", "POSTPAID"),
    ("DATA_ADDONS", "Data Add-ons", "CONSUMER_MOBILE", None),
    ("SHARED_DATA", "Shared Data", "CONSUMER_MOBILE", None),
    ("PREPAID", "Prepaid", "CONSUMER_MOBILE", None),
    ("MPLS", "MPLS", "ENTERPRISE_CONNECTIVITY", None),
    ("DIA", "Dedicated Internet Access", "ENTERPRISE_CONNECTIVITY", None),
    ("SD_WAN", "SD-WAN", "ENTERPRISE_CONNECTIVITY", None),
    ("PSTN", "PSTN", "LEGACY_VOICE", None),
    ("ISDN", "ISDN", "LEGACY_VOICE", None),
]


def seed_reference_data(session: Session, tenant_id: str) -> dict[str, int]:
    """Create demo reference data for a tenant. Existing codes are left unchanged."""
    created = {"portfolios": 0, "markets": 0, "product_families": 0}

    portfolios = {p.code: p for p in session.scalars(select(Portfolio).where(Portfolio.tenant_id == tenant_id))}
    for code, name in DEMO_PORTFOLIOS:
        if code not in portfolios:
            portfolios[code] = Portfolio(tenant_id=tenant_id, code=code, name=name, is_active=True)
            session.add(portfolios[code])
            created["portfolios"] += 1

    markets = {m.code for m in session.scalars(select(Market).where(Market.tenant_id == tenant_id))}
    for code, name, tz in DEMO_MARKETS:
        if code not in markets:
            session.add(Market(tenant_id=tenant_id, code=code, name=name, timezone=tz, is_active=True))
            created["markets"] += 1
    session.flush()

    families = {f.code: f for f in session.scalars(
        select(ProductFamily).where(ProductFamily.tenant_id == tenant_id))}
    for code, name, portfolio_code, parent_code in DEMO_FAMILIES:
        if code in families:
            continue
        family = ProductFamily(
            tenant_id=tenant_id, code=code, name=name, is_active=True,
            portfolio_id=portfolios[portfolio_code].id,
            parent_id=families[parent_code].id if parent_code else None,
        )
        session.add(family)
        session.flush()
        families[code] = family
        created["product_families"] += 1

    return created
