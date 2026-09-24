"""DCF engine (app/engine/dcf.py).

The WACC build-up and year-1 mechanics are pinned to values computed by hand
from the acme fixture (WACC ~13.53%, fair value ~₹183/share); the rest is
checked structurally — monotonic sensitivity, graceful unavailability, and the
LOW_CONFIDENCE flags that keep an over-sensitive DCF honest.
"""

from __future__ import annotations

import pytest

from app.engine.dcf import compute_dcf
from app.models.statements import MarketData


def test_wacc_build_up_matches_hand_calculation(acme):
    dcf = compute_dcf(acme.statements, acme.market)
    assert dcf.available
    # Re = 7 + 1.05*7 = 14.35; beta unclamped at 1.05.
    assert dcf.cost_of_equity_pct == pytest.approx(14.35, abs=0.01)
    assert dcf.beta_used == pytest.approx(1.05)
    # 3-year average effective tax ~24.7%, below the 25.17% statutory cap.
    assert dcf.tax_rate == pytest.approx(0.2472, abs=0.001)
    assert dcf.tax_rate <= 0.2517
    # WACC ~ 13.53% (hand-computed).
    assert dcf.wacc_pct == pytest.approx(13.53, abs=0.05)
    assert dcf.equity_weight + dcf.debt_weight == pytest.approx(1.0)


def test_fair_value_is_positive_and_bracketed(acme):
    dcf = compute_dcf(acme.statements, acme.market)
    # Hand-computed base ~ ₹183/share.
    assert dcf.fair_value_base == pytest.approx(183.2, abs=1.5)
    assert dcf.fair_value_low <= dcf.fair_value_base <= dcf.fair_value_high
    assert dcf.terminal_growth_pct == pytest.approx(5.5)
    assert dcf.confidence == "BASE"           # acme's range is tight


def test_forecast_has_horizon_years_of_fcff(acme):
    dcf = compute_dcf(acme.statements, acme.market)
    assert len(dcf.fcff_forecast) == 5
    assert len(dcf.revenue_growth_path_pct) == 5
    # Growth fades from the historical CAGR toward terminal g.
    assert dcf.revenue_growth_path_pct[0] > dcf.revenue_growth_path_pct[-1]
    assert dcf.revenue_growth_path_pct[-1] == pytest.approx(5.5)


def test_sensitivity_grid_is_monotonic(acme):
    dcf = compute_dcf(acme.statements, acme.market)
    grid = dcf.sensitivity
    assert grid is not None
    # Down a column (rising WACC) fair value falls.
    for j in range(len(grid.growth_values_pct)):
        column = [grid.grid[i][j] for i in range(len(grid.wacc_values_pct))]
        present = [c for c in column if c is not None]
        assert present == sorted(present, reverse=True)
    # Across a row (rising terminal growth) fair value rises.
    for i in range(len(grid.wacc_values_pct)):
        row = [c for c in grid.grid[i] if c is not None]
        assert row == sorted(row)


def test_unavailable_without_market_data(acme):
    dcf = compute_dcf(acme.statements, None)
    assert not dcf.available
    assert "market" in dcf.unavailable_reason.lower()


def test_clamped_beta_flags_low_confidence(acme):
    wild = MarketData(ticker="ACME.NS", cmp=150.0, shares_outstanding=100.0, beta=3.5)
    dcf = compute_dcf(acme.statements, wild)
    assert dcf.beta_used == pytest.approx(2.0)   # clamped to cap
    assert dcf.confidence == "LOW_CONFIDENCE"
    assert any("beta" in f for f in dcf.flags)


def test_wide_range_flags_low_confidence(levcyc):
    # The distressed cyclical sits near the WACC-g boundary: its fair-value
    # range is enormous, which must downgrade DCF confidence.
    dcf = compute_dcf(levcyc.statements, levcyc.market)
    if dcf.available:
        assert dcf.confidence == "LOW_CONFIDENCE"
        assert any("range" in f or "sensitive" in f for f in dcf.flags)


def test_dcf_is_deterministic(acme):
    a = compute_dcf(acme.statements, acme.market)
    b = compute_dcf(acme.statements, acme.market)
    assert a.model_dump() == b.model_dump()
