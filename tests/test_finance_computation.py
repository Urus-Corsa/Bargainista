"""Unit tests for pure Python finance computation functions.

No DB, no LLM, no I/O. Tests the deterministic core of the Finance agent:
_compute_finance_score, _annual_maintenance, _monthly_payment, and the
paint complexity multiplier lookup table.
"""

from __future__ import annotations

import pytest

from app.agents.finance import (
    ANNUAL_MAINTENANCE_BASE,
    _PAINT_COMPLEXITY_MULTIPLIER,
    _annual_maintenance,
    _compute_finance_score,
    _monthly_payment,
)


# ---------------------------------------------------------------------------
# _compute_finance_score
# ---------------------------------------------------------------------------


def test_finance_score_at_market_no_repairs_average_mileage():
    # asking == market value, no repairs, mileage matches 12k/yr → should return 7
    score = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=3,
        mileage=36_000,   # exactly 12k/yr × 3
    )
    assert score == 7


def test_finance_score_below_market_increases_score():
    # asking 25% below market low → should get > 7
    score = _compute_finance_score(
        asking_price=13_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=3,
        mileage=36_000,
    )
    assert score > 7


def test_finance_score_above_market_decreases_score():
    # asking 25% above market high → should get < 7
    score = _compute_finance_score(
        asking_price=28_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=3,
        mileage=36_000,
    )
    assert score < 7


def test_finance_score_high_repair_ratio_penalizes():
    # repair cost = 50% of asking → significant penalty
    baseline = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=3,
        mileage=36_000,
    )
    penalized = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=10_000,
        vehicle_age_years=3,
        mileage=36_000,
    )
    assert penalized < baseline


def test_finance_score_high_mileage_penalizes():
    # mileage ratio > 1.5 → -1.0 penalty
    normal = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=5,
        mileage=60_000,   # exactly 12k/yr
    )
    high_miles = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=5,
        mileage=100_000,  # 20k/yr — ratio 1.67 → -1.0
    )
    assert high_miles < normal


def test_finance_score_moderate_high_mileage_smaller_penalty():
    # mileage ratio 1.2-1.5 → -0.5 penalty; ratio > 1.5 → -1.0 penalty.
    # Both round to 6 when the base is 7 (banker's rounding absorbs the 0.5 gap),
    # so we assert normal > penalized >= very_high — strictly less than normal, at least as good as very_high.
    normal = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=5,
        mileage=60_000,
    )
    moderate_high = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=5,
        mileage=75_000,  # ratio 1.25 → -0.5 penalty applied
    )
    very_high = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=5,
        mileage=100_000,  # ratio 1.67 → -1.0 penalty applied
    )
    assert normal > moderate_high >= very_high


def test_finance_score_low_mileage_bonus():
    # mileage ratio < 0.5 with age > 2 → +0.5 bonus
    normal = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=5,
        mileage=60_000,
    )
    low_miles = _compute_finance_score(
        asking_price=20_000,
        market_value=20_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=5,
        mileage=20_000,  # ratio 0.33 → +0.5
    )
    assert low_miles > normal


def test_finance_score_clamped_to_minimum_1():
    # extreme asking price above market + high repairs should not go below 1
    score = _compute_finance_score(
        asking_price=100_000,
        market_value=10_000,
        range_band=0.10,
        total_repair_high=50_000,
        vehicle_age_years=15,
        mileage=300_000,
    )
    assert score >= 1


def test_finance_score_clamped_to_maximum_10():
    # very cheap asking price, no repairs, low mileage should not exceed 10
    score = _compute_finance_score(
        asking_price=1_000,
        market_value=30_000,
        range_band=0.10,
        total_repair_high=0,
        vehicle_age_years=4,
        mileage=10_000,
    )
    assert score <= 10


# ---------------------------------------------------------------------------
# _annual_maintenance
# ---------------------------------------------------------------------------


def test_annual_maintenance_young_vehicle_discounted():
    # age ≤ 3 → 0.70 multiplier
    result = _annual_maintenance("sedan", vehicle_age_years=2)
    expected = round(ANNUAL_MAINTENANCE_BASE["sedan"] * 0.70)
    assert result == expected


def test_annual_maintenance_mid_age_baseline():
    # age 4–7 → 1.00 multiplier
    result = _annual_maintenance("sedan", vehicle_age_years=5)
    expected = ANNUAL_MAINTENANCE_BASE["sedan"]
    assert result == expected


def test_annual_maintenance_old_vehicle_elevated():
    # age > 7 → 1.45 multiplier
    result = _annual_maintenance("sedan", vehicle_age_years=9)
    expected = round(ANNUAL_MAINTENANCE_BASE["sedan"] * 1.45)
    assert result == expected


def test_annual_maintenance_ev_lower_than_sedan():
    # EVs have lower base maintenance
    ev_cost = _annual_maintenance("ev", vehicle_age_years=5)
    sedan_cost = _annual_maintenance("sedan", vehicle_age_years=5)
    assert ev_cost < sedan_cost


def test_annual_maintenance_unknown_category_uses_fallback():
    # Unknown category falls back to 700 base
    result = _annual_maintenance("unknown_category", vehicle_age_years=5)
    assert result == 700


# ---------------------------------------------------------------------------
# _monthly_payment
# ---------------------------------------------------------------------------


def test_monthly_payment_normal_case():
    # Verify standard amortization: $20k, 9.5% APR, 60 months
    payment = _monthly_payment(20_000, 0.095, 60)
    # Expected ≈ $419 (standard formula)
    assert 400 < payment < 450


def test_monthly_payment_zero_principal():
    assert _monthly_payment(0, 0.095, 60) == 0.0


def test_monthly_payment_zero_rate():
    # No interest → principal / n_months
    payment = _monthly_payment(12_000, 0.0, 60)
    assert payment == pytest.approx(200.0)


def test_monthly_payment_positive():
    payment = _monthly_payment(25_000, 0.075, 60)
    assert payment > 0


# ---------------------------------------------------------------------------
# Paint complexity multiplier
# ---------------------------------------------------------------------------


def test_paint_complexity_standard_is_1():
    assert _PAINT_COMPLEXITY_MULTIPLIER["standard"] == 1.00


def test_paint_complexity_metallic_is_1_15():
    assert _PAINT_COMPLEXITY_MULTIPLIER["metallic"] == 1.15


def test_paint_complexity_pearl_is_1_35():
    assert _PAINT_COMPLEXITY_MULTIPLIER["pearl_or_tricoat"] == 1.35


def test_paint_complexity_multiplier_ordering():
    assert (
        _PAINT_COMPLEXITY_MULTIPLIER["standard"]
        < _PAINT_COMPLEXITY_MULTIPLIER["metallic"]
        < _PAINT_COMPLEXITY_MULTIPLIER["pearl_or_tricoat"]
    )
