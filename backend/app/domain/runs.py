"""Run (assessment) rules: states, objectives and Define Scope validation (field spec tab 02).

Pure functions only. Reference checks that need the database (does the portfolio exist, does the
family belong to it, is the owner eligible) live in ``app.services.runs``.
"""

import calendar
from dataclasses import dataclass, field
from datetime import date
from enum import StrEnum

from app.domain.common import FieldError

NAME_MIN, NAME_MAX = 3, 120
MARKETS_MIN, MARKETS_MAX = 1, 10
FAMILIES_MIN, FAMILIES_MAX = 1, 100
MAX_PERIOD_MONTHS = 36
DEFAULT_PERIOD_MONTHS = 12


class RunState(StrEnum):
    CONFIGURING = "CONFIGURING"
    INGESTING = "INGESTING"
    VALIDATING = "VALIDATING"
    ANALYSING = "ANALYSING"
    REVIEW = "REVIEW"
    APPROVAL = "APPROVAL"
    APPROVED = "APPROVED"
    FAILED = "FAILED"
    CLOSED = "CLOSED"


# Runs still being worked on (counted as "Active runs" on the Assessments home).
IN_PROGRESS_STATES = frozenset({
    RunState.CONFIGURING, RunState.INGESTING, RunState.VALIDATING, RunState.ANALYSING,
    RunState.REVIEW, RunState.APPROVAL,
})
# Scope can only be edited before analysis starts.
SCOPE_EDITABLE_STATES = frozenset({RunState.CONFIGURING})

STATE_LABELS: dict[RunState, str] = {
    RunState.CONFIGURING: "Configuring",
    RunState.INGESTING: "Ingesting",
    RunState.VALIDATING: "Validating",
    RunState.ANALYSING: "Analysing",
    RunState.REVIEW: "Review",
    RunState.APPROVAL: "Approval",
    RunState.APPROVED: "Approved",
    RunState.FAILED: "Failed",
    RunState.CLOSED: "Closed",
}


class Objective(StrEnum):
    REDUCE_DUPLICATES = "REDUCE_DUPLICATES"
    BSS_SUNSET = "BSS_SUNSET"
    REDUCE_FALLOUT = "REDUCE_FALLOUT"
    IMPROVE_MARGIN = "IMPROVE_MARGIN"
    MIGRATION_READINESS = "MIGRATION_READINESS"
    PORTFOLIO_STANDARDISATION = "PORTFOLIO_STANDARDISATION"


OBJECTIVE_LABELS: dict[Objective, str] = {
    Objective.REDUCE_DUPLICATES: "Reduce duplicate offers",
    Objective.BSS_SUNSET: "Prepare BSS sunset",
    Objective.REDUCE_FALLOUT: "Reduce order fallout",
    Objective.IMPROVE_MARGIN: "Improve margin",
    Objective.MIGRATION_READINESS: "Accelerate migration",
    Objective.PORTFOLIO_STANDARDISATION: "Standardise portfolio",
}

# Personas that may be the accountable owner of a run.
OWNER_ROLES = ("portfolio_manager", "product_owner")


@dataclass
class ScopeInput:
    name: str
    portfolio_id: object
    market_ids: list = field(default_factory=list)
    product_family_ids: list = field(default_factory=list)
    period_start: date | None = None
    period_end: date | None = None
    objectives: list[str] = field(default_factory=list)
    owner_id: str | None = None


def add_months(d: date, months: int) -> date:
    """Calendar month arithmetic; clamps to the last day of the month (31 Mar - 1 month = 28/29 Feb)."""
    total = d.year * 12 + (d.month - 1) + months
    year, month = divmod(total, 12)
    month += 1
    return date(year, month, min(d.day, calendar.monthrange(year, month)[1]))


def default_period(today: date) -> tuple[date, date]:
    return add_months(today, -DEFAULT_PERIOD_MONTHS), today


def normalise_name(name: str) -> str:
    return " ".join(name.split())


def validate_scope(scope: ScopeInput) -> list[FieldError]:
    """Field rules from the spec that need no database. Returns all errors, not just the first."""
    errors: list[FieldError] = []

    name = normalise_name(scope.name or "")
    if not name:
        errors.append(FieldError("name", "Run name is required"))
    elif not NAME_MIN <= len(name) <= NAME_MAX:
        errors.append(FieldError("name", f"Run name must be {NAME_MIN}–{NAME_MAX} characters"))

    if not scope.portfolio_id:
        errors.append(FieldError("portfolio_id", "Portfolio is required"))

    markets = list(dict.fromkeys(scope.market_ids or []))
    if not MARKETS_MIN <= len(markets) <= MARKETS_MAX:
        errors.append(FieldError("market_ids", f"Select {MARKETS_MIN}–{MARKETS_MAX} markets"))

    families = list(dict.fromkeys(scope.product_family_ids or []))
    if not FAMILIES_MIN <= len(families) <= FAMILIES_MAX:
        errors.append(FieldError("product_family_ids",
                                 f"Select {FAMILIES_MIN}–{FAMILIES_MAX} product families"))

    if scope.period_start is None:
        errors.append(FieldError("period_start", "Period start is required"))
    if scope.period_end is None:
        errors.append(FieldError("period_end", "Period end is required"))
    if scope.period_start and scope.period_end:
        if scope.period_start > scope.period_end:
            errors.append(FieldError("period_end", "Period end must be on or after the start"))
        elif scope.period_end > add_months(scope.period_start, MAX_PERIOD_MONTHS):
            errors.append(FieldError("period_end", f"The period can be at most {MAX_PERIOD_MONTHS} months"))

    objectives = list(dict.fromkeys(scope.objectives or []))
    unknown = [o for o in objectives if o not in Objective.__members__]
    if unknown:
        errors.append(FieldError("objectives", f"Unknown objective: {', '.join(unknown)}"))
    elif not objectives:
        errors.append(FieldError("objectives", "Select at least one business objective"))

    if not scope.owner_id:
        errors.append(FieldError("owner_id", "Accountable owner is required"))

    return errors


def format_run_code(number: int) -> str:
    return f"RUN-{number:04d}"
