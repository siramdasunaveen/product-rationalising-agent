import uuid
from datetime import date

import pytest

from app.domain.runs import (
    ScopeInput,
    add_months,
    default_period,
    format_run_code,
    validate_scope,
)


def _valid(**over) -> ScopeInput:
    base = dict(
        name="Consumer Mobile Q4", portfolio_id=uuid.uuid4(), market_ids=[uuid.uuid4()],
        product_family_ids=[uuid.uuid4()], period_start=date(2025, 11, 16), period_end=date(2026, 11, 16),
        objectives=["REDUCE_DUPLICATES"], owner_id="u1",
    )
    return ScopeInput(**(base | over))


def fields(errors):
    return {e.field for e in errors}


def test_valid_scope_has_no_errors():
    assert validate_scope(_valid()) == []


def test_all_errors_reported_at_once():
    errors = validate_scope(ScopeInput(name="", portfolio_id=None))
    assert fields(errors) == {"name", "portfolio_id", "market_ids", "product_family_ids",
                              "period_start", "period_end", "objectives", "owner_id"}


@pytest.mark.parametrize("name,ok", [("ab", False), ("abc", True), ("x" * 120, True), ("x" * 121, False),
                                     ("   a   b  ", True)])
def test_name_length_after_trimming(name, ok):
    assert ("name" not in fields(validate_scope(_valid(name=name)))) is ok


def test_markets_between_1_and_10():
    assert "market_ids" in fields(validate_scope(_valid(market_ids=[])))
    assert "market_ids" in fields(validate_scope(_valid(market_ids=[uuid.uuid4() for _ in range(11)])))
    assert "market_ids" not in fields(validate_scope(_valid(market_ids=[uuid.uuid4() for _ in range(10)])))


def test_duplicate_ids_count_once():
    m = uuid.uuid4()
    assert validate_scope(_valid(market_ids=[m] * 11)) == []


def test_families_between_1_and_100():
    assert "product_family_ids" in fields(validate_scope(_valid(product_family_ids=[])))
    too_many = [uuid.uuid4() for _ in range(101)]
    assert "product_family_ids" in fields(validate_scope(_valid(product_family_ids=too_many)))


def test_period_order_and_maximum():
    assert "period_end" in fields(validate_scope(_valid(period_start=date(2026, 2, 1),
                                                        period_end=date(2026, 1, 31))))
    # exactly 36 months is allowed, one day more is not
    assert validate_scope(_valid(period_start=date(2023, 1, 1), period_end=date(2026, 1, 1))) == []
    assert "period_end" in fields(validate_scope(_valid(period_start=date(2023, 1, 1),
                                                        period_end=date(2026, 1, 2))))
    # same-day period is allowed
    assert validate_scope(_valid(period_start=date(2026, 1, 1), period_end=date(2026, 1, 1))) == []


def test_objectives_required_and_known():
    assert "objectives" in fields(validate_scope(_valid(objectives=[])))
    assert "objectives" in fields(validate_scope(_valid(objectives=["WORLD_PEACE"])))
    all_six = ["REDUCE_DUPLICATES", "BSS_SUNSET", "REDUCE_FALLOUT", "IMPROVE_MARGIN",
               "MIGRATION_READINESS", "PORTFOLIO_STANDARDISATION"]
    assert validate_scope(_valid(objectives=all_six)) == []


def test_month_arithmetic_clamps_to_month_end():
    assert add_months(date(2026, 3, 31), -1) == date(2026, 2, 28)
    assert add_months(date(2024, 2, 29), -12) == date(2023, 2, 28)
    assert add_months(date(2026, 11, 16), -12) == date(2025, 11, 16)


def test_default_period_is_last_12_months():
    assert default_period(date(2026, 11, 16)) == (date(2025, 11, 16), date(2026, 11, 16))


def test_run_code_format():
    assert format_run_code(1) == "RUN-0001"
    assert format_run_code(12345) == "RUN-12345"
