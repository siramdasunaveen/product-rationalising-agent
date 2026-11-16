"""Run Service: create, edit, list and summarise runs (screens 01 and 02)."""

import uuid
from dataclasses import dataclass, fields, replace
from datetime import UTC, datetime

from sqlalchemy import func, or_, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.db.models import Market, Portfolio, ProductFamily, Run
from app.domain.runs import (
    IN_PROGRESS_STATES,
    OWNER_ROLES,
    SCOPE_EDITABLE_STATES,
    FieldError,
    RunState,
    ScopeInput,
    format_run_code,
    normalise_name,
    validate_scope,
)
from app.ports.audit import AuditLog, AuditRecord
from app.ports.auth import Principal
from app.ports.people import PeopleDirectory


class RunError(Exception):
    status_code = 400
    code = "run_error"

    def __init__(self, message: str, errors: list[FieldError] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.errors = errors or []


class RunValidationError(RunError):
    status_code = 422
    code = "validation_failed"


class RunNameTaken(RunError):
    status_code = 409
    code = "name_taken"


class RunVersionConflict(RunError):
    status_code = 409
    code = "version_conflict"


class RunNotEditable(RunError):
    status_code = 409
    code = "not_editable"


class RunNotFound(RunError):
    status_code = 404
    code = "not_found"


@dataclass(frozen=True)
class RunSummary:
    active_runs: int
    opportunities: int
    awaiting_approval: int
    blocked: int


SCOPE_FIELDS = tuple(f.name for f in fields(ScopeInput))


def _check_references(session: Session, people: PeopleDirectory, tenant_id: str,
                      scope: ScopeInput) -> tuple[list[FieldError], str | None]:
    """Database-backed checks. Returns (errors, owner display name)."""
    errors: list[FieldError] = []

    portfolio = None
    if scope.portfolio_id:
        portfolio = session.scalar(select(Portfolio).where(
            Portfolio.id == scope.portfolio_id, Portfolio.tenant_id == tenant_id,
            Portfolio.is_active.is_(True)))
        if portfolio is None:
            errors.append(FieldError("portfolio_id", "Unknown portfolio"))

    if scope.market_ids:
        found = set(session.scalars(select(Market.id).where(
            Market.tenant_id == tenant_id, Market.is_active.is_(True), Market.id.in_(scope.market_ids))))
        if found != set(scope.market_ids):
            errors.append(FieldError("market_ids", "Unknown market selected"))

    if scope.product_family_ids:
        rows = session.execute(select(ProductFamily.id, ProductFamily.portfolio_id).where(
            ProductFamily.tenant_id == tenant_id, ProductFamily.is_active.is_(True),
            ProductFamily.id.in_(scope.product_family_ids))).all()
        if {r.id for r in rows} != set(scope.product_family_ids):
            errors.append(FieldError("product_family_ids", "Unknown product family selected"))
        elif portfolio is not None and any(r.portfolio_id != portfolio.id for r in rows):
            errors.append(FieldError("product_family_ids",
                                     "Product families must belong to the selected portfolio"))

    owner_name = None
    if scope.owner_id:
        person = people.get(tenant_id, scope.owner_id, session=session)
        if person is None:
            errors.append(FieldError("owner_id", "Unknown owner"))
        elif not set(person.roles) & set(OWNER_ROLES):
            errors.append(FieldError("owner_id", "Owner must be a Portfolio Manager or Product Owner"))
        else:
            owner_name = person.name

    return errors, owner_name


def _name_taken(session: Session, tenant_id: str, name: str, exclude_id: uuid.UUID | None = None) -> bool:
    stmt = select(Run.id).where(Run.tenant_id == tenant_id, Run.state != RunState.CLOSED,
                                func.lower(Run.name) == name.lower())
    if exclude_id:
        stmt = stmt.where(Run.id != exclude_id)
    return session.scalar(stmt) is not None


def _validated(session: Session, people: PeopleDirectory, tenant_id: str, scope: ScopeInput,
               exclude_id: uuid.UUID | None = None) -> tuple[ScopeInput, str]:
    scope = replace(
        scope,
        name=normalise_name(scope.name or ""),
        market_ids=list(dict.fromkeys(scope.market_ids or [])),
        product_family_ids=list(dict.fromkeys(scope.product_family_ids or [])),
        objectives=list(dict.fromkeys(scope.objectives or [])),
    )
    errors = validate_scope(scope)
    ref_errors, owner_name = _check_references(session, people, tenant_id, scope)
    errors += ref_errors
    if errors:
        raise RunValidationError("Scope is not valid", errors)
    if _name_taken(session, tenant_id, scope.name, exclude_id):
        raise RunNameTaken("Name already used",
                           [FieldError("name", "An active run with this name already exists")])
    return scope, owner_name or ""


def _audit(audit: AuditLog, session: Session, principal: Principal, action: str, run: Run,
           payload: dict) -> None:
    audit.record(
        AuditRecord(
            tenant_id=principal.tenant_id, actor_id=principal.user_id, actor_roles=principal.roles,
            auth_context=principal.auth_context, action=action, entity_type="run",
            entity_id=str(run.id), payload={"run_code": run.run_code, **payload},
        ),
        session=session,
    )


def _scope_payload(run: Run) -> dict:
    return {
        "name": run.name, "portfolio_id": str(run.portfolio_id),
        "market_ids": [str(m) for m in run.market_ids],
        "product_family_ids": [str(f) for f in run.product_family_ids],
        "period_start": run.period_start.isoformat(), "period_end": run.period_end.isoformat(),
        "objectives": list(run.objectives), "owner_id": run.owner_id,
    }


def create_run(session: Session, audit: AuditLog, people: PeopleDirectory, principal: Principal,
               scope: ScopeInput) -> Run:
    tenant = principal.tenant_id
    scope, owner_name = _validated(session, people, tenant, scope)

    # Run codes are sequential per tenant; serialise allocation for this transaction.
    session.execute(text("SELECT pg_advisory_xact_lock(hashtextextended(:k, 0))"), {"k": f"run_code:{tenant}"})
    count = session.scalar(select(func.count()).select_from(Run).where(Run.tenant_id == tenant)) or 0

    run = Run(
        tenant_id=tenant, run_code=format_run_code(count + 1), name=scope.name,
        state=RunState.CONFIGURING.value, portfolio_id=scope.portfolio_id,
        market_ids=scope.market_ids, product_family_ids=scope.product_family_ids,
        period_start=scope.period_start, period_end=scope.period_end, objectives=scope.objectives,
        owner_id=scope.owner_id, owner_name=owner_name, created_by=principal.user_id, version=1,
    )
    session.add(run)
    try:
        session.flush()
    except IntegrityError as exc:  # concurrent create with the same name
        session.rollback()
        raise RunNameTaken("Name already used",
                           [FieldError("name", "An active run with this name already exists")]) from exc
    _audit(audit, session, principal, "run.created", run, {"scope": _scope_payload(run)})
    session.commit()
    return run


def get_run(session: Session, tenant_id: str, run_id: uuid.UUID, *, for_update: bool = False) -> Run:
    stmt = select(Run).where(Run.id == run_id, Run.tenant_id == tenant_id)
    if for_update:
        stmt = stmt.with_for_update()
    run = session.scalar(stmt)
    if run is None:
        raise RunNotFound("Run not found")
    return run


def update_run_scope(session: Session, audit: AuditLog, people: PeopleDirectory, principal: Principal,
                     run_id: uuid.UUID, changes: dict, expected_version: int) -> Run:
    run = get_run(session, principal.tenant_id, run_id, for_update=True)
    if run.state not in SCOPE_EDITABLE_STATES:
        raise RunNotEditable(f"Scope can only be changed while the run is configuring (now {run.state})")
    if run.version != expected_version:
        raise RunVersionConflict("This run was changed by someone else. Reload and try again.")

    current = ScopeInput(
        name=run.name, portfolio_id=run.portfolio_id, market_ids=list(run.market_ids),
        product_family_ids=list(run.product_family_ids), period_start=run.period_start,
        period_end=run.period_end, objectives=list(run.objectives), owner_id=run.owner_id,
    )
    merged = replace(current, **{k: v for k, v in changes.items() if k in SCOPE_FIELDS})
    merged, owner_name = _validated(session, people, principal.tenant_id, merged, exclude_id=run.id)

    before = _scope_payload(run)
    for name in SCOPE_FIELDS:
        setattr(run, name, getattr(merged, name))
    run.owner_name = owner_name
    after = _scope_payload(run)
    changed = sorted(k for k in after if after[k] != before[k])
    if not changed:
        session.rollback()  # release the row lock; nothing to save
        return get_run(session, principal.tenant_id, run_id)

    run.version += 1
    run.updated_at = datetime.now(UTC)
    session.flush()
    _audit(audit, session, principal, "run.scope_updated", run,
           {"version": run.version, "changed_fields": changed,
            "before": {k: before[k] for k in changed}, "after": {k: after[k] for k in changed}})
    session.commit()
    return run


def list_runs(session: Session, tenant_id: str, *, query: str = "", limit: int = 50,
              offset: int = 0) -> tuple[list[tuple[Run, str]], int]:
    """Runs with their portfolio name, newest activity first. Search: run name/code, portfolio, owner."""
    stmt = (select(Run, Portfolio.name).join(Portfolio, Portfolio.id == Run.portfolio_id)
            .where(Run.tenant_id == tenant_id))
    q = query.strip()
    if q:
        like = f"%{q}%"
        stmt = stmt.where(or_(Run.name.ilike(like), Run.run_code.ilike(like),
                              Portfolio.name.ilike(like), Run.owner_name.ilike(like)))
    total = session.scalar(select(func.count()).select_from(stmt.subquery())) or 0
    rows = session.execute(stmt.order_by(Run.updated_at.desc(), Run.run_code.desc())
                           .limit(limit).offset(offset)).all()
    return [(r[0], r[1]) for r in rows], total


def summarise(session: Session, tenant_id: str) -> RunSummary:
    active = session.scalar(select(func.count()).select_from(Run).where(
        Run.tenant_id == tenant_id, Run.state.in_([s.value for s in IN_PROGRESS_STATES]))) or 0
    # Opportunities, approvals and blockers are produced by later steps (analysis, decisions,
    # readiness); until those exist they are genuinely zero.
    return RunSummary(active_runs=active, opportunities=0, awaiting_approval=0, blocked=0)
