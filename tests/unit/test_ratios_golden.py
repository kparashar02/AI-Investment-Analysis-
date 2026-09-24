"""Golden-value tests for the ratio engine (PRD 19.1, evaluation Track 1).

Every expected value below is written out **longhand from the raw statement
figures**, not copied from the engine's own output. That matters: a test that
asserts the engine agrees with itself is worthless. Writing the arithmetic
out a second way, in a different file, is what makes this an independent
check.

Tolerance here is ``rel=1e-9`` — effectively exact — because the synthetic
fixture was constructed to have closed-form values. The 0.5% tolerance the
PRD specifies applies to real-company golden files, where hand-entered
figures and rounding in published statements make exactness unattainable.
"""

from __future__ import annotations

import math

import pytest

pytestmark = pytest.mark.golden

EXACT = {"rel": 1e-9}


# ---------------------------------------------------------------------------
# Acme FY2026 raw figures, restated here so the expectations below are
# readable without opening the JSON. If these drift from the fixture, the
# fixture-integrity test at the bottom of this file fails.
# ---------------------------------------------------------------------------
REVENUE = 10000.0
COGS = 6000.0
GROSS_PROFIT = 4000.0
EBITDA = 2500.0
DANDA = 500.0
EBIT = 2000.0
INTEREST = 200.0
PBT = 1900.0
TAX = 475.0
PAT = 1425.0
SHARES = 100.0
DPS = 5.0

CASH = 800.0
INVENTORY = 1200.0
RECEIVABLES = 1500.0
TCA = 3800.0
NFA = 4000.0
TOTAL_ASSETS = 9000.0
PAYABLES = 900.0
STD = 400.0
TCL = 1800.0
LTD = 1600.0
TOTAL_LIABILITIES = 4000.0
EQUITY = 5000.0

OCF = 1900.0
CAPEX = 700.0
FCF = 1200.0
DIVIDENDS_PAID = 500.0

# FY2025, needed for the two-point averages.
PREV_TOTAL_ASSETS = 7600.0
PREV_EQUITY = 3800.0
PREV_INVENTORY = 1000.0
PREV_RECEIVABLES = 1200.0
PREV_PAYABLES = 800.0
PREV_NFA = 3500.0
PREV_PAT = 975.0

CMP = 150.0
MARKET_CAP = CMP * SHARES          # 15,000 Cr

AVG_ASSETS = (TOTAL_ASSETS + PREV_TOTAL_ASSETS) / 2      # 8,300
AVG_EQUITY = (EQUITY + PREV_EQUITY) / 2                  # 4,400


def value(metric_set, name):
    metric = metric_set.get(name)
    assert metric is not None, f"metric '{name}' was not computed at all"
    return metric.value


# ---------------------------------------------------------------- margins ---
def test_margins(acme_metrics):
    assert value(acme_metrics, "gross_margin") == pytest.approx(
        GROSS_PROFIT / REVENUE * 100, **EXACT)          # 40.00%
    assert value(acme_metrics, "ebitda_margin") == pytest.approx(
        EBITDA / REVENUE * 100, **EXACT)                # 25.00%
    assert value(acme_metrics, "ebit_margin") == pytest.approx(
        EBIT / REVENUE * 100, **EXACT)                  # 20.00%
    assert value(acme_metrics, "net_margin") == pytest.approx(
        PAT / REVENUE * 100, **EXACT)                   # 14.25%
    assert value(acme_metrics, "effective_tax_rate") == pytest.approx(
        TAX / PBT * 100, **EXACT)                       # 25.00%


# ---------------------------------------------------------------- returns ---
def test_roe_uses_average_equity(acme_metrics):
    """ROE divides a full year's profit by average equity, not closing equity.

    Closing equity would give 28.50%; the average basis gives 32.39%. The
    difference is 3.9 percentage points on a company that grew equity 32% in
    the year, which is more than enough to move a threshold band.
    """
    assert value(acme_metrics, "roe") == pytest.approx(PAT / AVG_EQUITY * 100, **EXACT)
    assert value(acme_metrics, "roe") == pytest.approx(32.386363636363, rel=1e-9)
    assert value(acme_metrics, "roe") != pytest.approx(PAT / EQUITY * 100, rel=1e-6)


def test_roa_uses_average_assets(acme_metrics):
    assert value(acme_metrics, "roa") == pytest.approx(PAT / AVG_ASSETS * 100, **EXACT)


def test_roce_uses_closing_capital_employed(acme_metrics):
    expected = EBIT / (TOTAL_ASSETS - TCL) * 100         # 2000 / 7200 = 27.78%
    assert value(acme_metrics, "roce") == pytest.approx(expected, **EXACT)


def test_roic_uses_effective_tax_rate(acme_metrics):
    nopat = EBIT * (1 - TAX / PBT)                       # 2000 x 0.75 = 1500
    invested = (STD + LTD) + EQUITY - CASH               # 2000 + 5000 - 800 = 6200
    assert value(acme_metrics, "roic") == pytest.approx(nopat / invested * 100, **EXACT)


# --------------------------------------------------------------- leverage ---
def test_leverage_and_coverage(acme_metrics):
    total_debt = STD + LTD
    assert value(acme_metrics, "debt_to_equity") == pytest.approx(
        total_debt / EQUITY, **EXACT)                    # 0.40
    assert value(acme_metrics, "net_debt_to_ebitda") == pytest.approx(
        (total_debt - CASH) / EBITDA, **EXACT)           # 1200 / 2500 = 0.48
    assert value(acme_metrics, "interest_coverage") == pytest.approx(
        EBIT / INTEREST, **EXACT)                        # 10.0x
    assert value(acme_metrics, "dscr") == pytest.approx(
        (EBITDA - TAX) / (INTEREST + STD), **EXACT)      # 2025 / 600 = 3.375
    assert value(acme_metrics, "equity_multiplier") == pytest.approx(
        TOTAL_ASSETS / EQUITY, **EXACT)                  # 1.80


# -------------------------------------------------------------- liquidity ---
def test_liquidity(acme_metrics):
    assert value(acme_metrics, "current_ratio") == pytest.approx(TCA / TCL, **EXACT)
    assert value(acme_metrics, "quick_ratio") == pytest.approx(
        (TCA - INVENTORY) / TCL, **EXACT)                # 2600 / 1800
    assert value(acme_metrics, "net_working_capital") == pytest.approx(
        TCA - TCL, **EXACT)                              # 2000 Cr


# ------------------------------------------------------------- efficiency ---
def test_working_capital_days(acme_metrics):
    avg_inv = (INVENTORY + PREV_INVENTORY) / 2           # 1100
    avg_rec = (RECEIVABLES + PREV_RECEIVABLES) / 2       # 1350
    avg_pay = (PAYABLES + PREV_PAYABLES) / 2             # 850

    inv_days = avg_inv / COGS * 365
    rec_days = avg_rec / REVENUE * 365
    pay_days = avg_pay / COGS * 365

    assert value(acme_metrics, "inventory_days") == pytest.approx(inv_days, **EXACT)
    assert value(acme_metrics, "receivable_days") == pytest.approx(rec_days, **EXACT)
    assert value(acme_metrics, "payable_days") == pytest.approx(pay_days, **EXACT)
    assert value(acme_metrics, "cash_conversion_cycle") == pytest.approx(
        inv_days + rec_days - pay_days, **EXACT)         # ~64.48 days


def test_turnover(acme_metrics):
    assert value(acme_metrics, "asset_turnover") == pytest.approx(
        REVENUE / AVG_ASSETS, **EXACT)
    assert value(acme_metrics, "fixed_asset_turnover") == pytest.approx(
        REVENUE / ((NFA + PREV_NFA) / 2), **EXACT)
    assert value(acme_metrics, "nwc_to_revenue") == pytest.approx(
        (TCA - TCL) / REVENUE * 100, **EXACT)            # 20.00%


# -------------------------------------------------------- per share/market --
def test_per_share(acme_metrics):
    eps = PAT / SHARES
    assert value(acme_metrics, "eps") == pytest.approx(eps, **EXACT)          # 14.25
    assert value(acme_metrics, "bvps") == pytest.approx(EQUITY / SHARES, **EXACT)  # 50.00
    assert value(acme_metrics, "dividend_payout") == pytest.approx(
        DPS / eps * 100, **EXACT)                        # 35.09%


def test_valuation_multiples(acme_metrics):
    eps = PAT / SHARES
    bvps = EQUITY / SHARES
    net_debt = (STD + LTD) - CASH
    ev = MARKET_CAP + net_debt                           # 15000 + 1200 = 16200

    assert value(acme_metrics, "pe_ratio") == pytest.approx(CMP / eps, **EXACT)
    assert value(acme_metrics, "pb_ratio") == pytest.approx(CMP / bvps, **EXACT)   # 3.0
    assert value(acme_metrics, "ev_ebitda") == pytest.approx(ev / EBITDA, **EXACT)  # 6.48
    assert value(acme_metrics, "ev_sales") == pytest.approx(ev / REVENUE, **EXACT)  # 1.62
    assert value(acme_metrics, "p_fcf") == pytest.approx(MARKET_CAP / FCF, **EXACT)  # 12.5
    assert value(acme_metrics, "dividend_yield") == pytest.approx(
        DPS / CMP * 100, **EXACT)                        # 3.33%
    assert value(acme_metrics, "earnings_yield") == pytest.approx(
        eps / CMP * 100, **EXACT)                        # 9.50%


def test_peg_uses_latest_eps_growth(acme_metrics):
    eps = PAT / SHARES
    prev_eps = PREV_PAT / SHARES
    growth_pct = (eps / prev_eps - 1) * 100              # 46.15%
    assert value(acme_metrics, "peg_ratio") == pytest.approx(
        (CMP / eps) / growth_pct, **EXACT)


# -------------------------------------------------------------- cash flow ---
def test_cash_flow_quality(acme_metrics):
    assert value(acme_metrics, "ocf_to_pat") == pytest.approx(OCF / PAT, **EXACT)
    assert value(acme_metrics, "ocf_margin") == pytest.approx(
        OCF / REVENUE * 100, **EXACT)                    # 19.00%
    assert value(acme_metrics, "fcf_margin") == pytest.approx(
        FCF / REVENUE * 100, **EXACT)                    # 12.00%
    assert value(acme_metrics, "capex_intensity") == pytest.approx(
        CAPEX / REVENUE * 100, **EXACT)                  # 7.00%
    assert value(acme_metrics, "dividend_coverage") == pytest.approx(
        FCF / DIVIDENDS_PAID, **EXACT)                   # 2.40x
    assert value(acme_metrics, "free_cash_flow") == pytest.approx(OCF - CAPEX, **EXACT)


# ------------------------------------------------------- fixture integrity --
def test_fixture_matches_the_constants_used_above(acme):
    """Guard against the fixture and this test file drifting apart.

    Without this, editing the JSON would silently invalidate every
    expectation above while the suite still passed.
    """
    period = acme.statements.latest
    assert period.label == "FY2026"
    assert period.income.revenue == REVENUE
    assert period.income.pat == PAT
    assert period.balance.total_assets == TOTAL_ASSETS
    assert period.balance.shareholders_equity == EQUITY
    assert period.cashflow.operating_cash_flow == OCF
    assert acme.market is not None
    assert acme.market.cmp == CMP
    assert acme.market.market_cap == pytest.approx(MARKET_CAP)


def test_balance_sheets_balance_in_every_period(acme, levcyc):
    """A balance sheet that does not balance invalidates every ratio built on it."""
    for golden in (acme, levcyc):
        for period in golden.statements.annual:
            passed, deviation = period.balance.balance_check()
            assert passed, (
                f"{golden.statements.ticker} {period.label} does not balance "
                f"(deviation {deviation}% of total assets)"
            )
            assert deviation == pytest.approx(0.0, abs=1e-9)


# ----------------------------------------------------- failure-path company --
def test_distressed_company_ratios(levcyc_metrics):
    """The values that veto rules V2, V3 and V4 will read in Phase 2."""
    # V2 leg 1: net debt / EBITDA above 5.0
    net_debt = (600.0 + 1400.0) - 100.0                  # 1900
    assert value(levcyc_metrics, "net_debt_to_ebitda") == pytest.approx(
        net_debt / 330.0, **EXACT)
    assert value(levcyc_metrics, "net_debt_to_ebitda") > 5.0

    # V2 leg 2: interest coverage below 1.5
    assert value(levcyc_metrics, "interest_coverage") == pytest.approx(
        150.0 / 120.0, **EXACT)                          # 1.25x
    assert value(levcyc_metrics, "interest_coverage") < 1.5

    # V3 input: cash conversion below 0.5
    assert value(levcyc_metrics, "ocf_to_pat") == pytest.approx(15.0 / 33.0, **EXACT)
    assert value(levcyc_metrics, "ocf_to_pat") < 0.5

    assert value(levcyc_metrics, "debt_to_equity") == pytest.approx(
        2000.0 / 600.0, **EXACT)                         # 3.33x


def test_negative_fcf_suppresses_price_to_fcf(levcyc_metrics):
    """A negative-FCF multiple is meaningless, so it must not be reported.

    Dividing a positive market cap by negative free cash flow yields a
    negative multiple that a naive threshold curve would score as *cheap*.
    Suppressing it is the only correct behaviour.
    """
    metric = levcyc_metrics.get("p_fcf")
    assert metric.value is None
    assert metric.final_score is None
    assert "non-positive" in (metric.unavailable_reason or "")

    assert value(levcyc_metrics, "free_cash_flow") == pytest.approx(-145.0, **EXACT)


def test_no_market_data_leaves_valuation_unavailable(acme):
    """Without a price, every market-based metric is unavailable — not zero."""
    from app.engine.registry import compute_metric_set

    metric_set = compute_metric_set(acme.statements, market=None)
    for name in ("pe_ratio", "pb_ratio", "ev_ebitda", "ev_sales", "p_fcf",
                 "dividend_yield", "earnings_yield"):
        metric = metric_set.get(name)
        assert metric is not None, f"{name} should still appear in the metric set"
        assert metric.value is None, f"{name} must be unavailable without market data"
        assert metric.final_score is None

    # Statement-derived metrics are unaffected.
    assert metric_set.value("roe") == pytest.approx(PAT / AVG_EQUITY * 100, **EXACT)
    assert any("No market data" in w for w in metric_set.warnings)


def test_every_metric_carries_its_working(acme_metrics):
    """Auditability is a hard requirement, not a nicety (PRD UI2, NF14)."""
    for name, metric in acme_metrics.metrics.items():
        if not metric.available:
            assert metric.unavailable_reason, f"{name} is n/a with no stated reason"
            continue
        assert metric.formula, f"{name} has a value but no formula string"
        assert not math.isnan(metric.value), f"{name} is NaN"
        # Growth metrics carry their endpoints; screen scores carry a count.
        assert metric.inputs_used, f"{name} records no inputs"
