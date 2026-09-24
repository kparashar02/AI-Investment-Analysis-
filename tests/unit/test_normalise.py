"""Provider payload → canonical schema (app/data/normalise.py).

These tests pin the three normalisations the data layer is responsible for —
field mapping, crore/share-count units, and sign correction — plus the
data-quality report that drives veto V1. They use hand-written raw payloads in
yfinance's own vocabulary and never import yfinance or touch the network.
"""

from __future__ import annotations

import copy

import pytest

from app.data.normalise import (
    CRORE,
    build_data_quality,
    normalise_market,
    normalise_statements,
)


def _annual_period(period_end: str) -> dict:
    """A complete, self-consistent yfinance-shaped annual period.

    Values are absolute rupees; the balance sheet balances exactly
    (assets 150,000 cr = liabilities 60,000 cr + equity 90,000 cr).
    """
    return {
        "period_end": period_end,
        "income": {
            "Total Revenue": 2.0e12,          # 200,000 cr
            "Cost Of Revenue": 1.2e12,        # 120,000 cr -> gross profit derives to 80,000
            "EBITDA": 5.0e11,                 # 50,000 cr
            "Reconciled Depreciation": 1.0e11,  # 10,000 cr
            "Operating Income": 4.0e11,       # 40,000 cr (EBIT)
            "Interest Expense": 5.0e10,       # 5,000 cr
            "Pretax Income": 3.6e11,          # 36,000 cr
            "Tax Provision": 9.0e10,          # 9,000 cr
            "Net Income": 2.7e11,             # 27,000 cr
            "Diluted Average Shares": 1.0e10,  # 1,000 cr shares -> EPS 27.0
        },
        "balance": {
            "Cash And Cash Equivalents": 3.0e11,   # 30,000 cr
            "Inventory": 1.0e11,                    # 10,000 cr
            "Receivables": 1.5e11,                  # 15,000 cr
            "Current Assets": 7.0e11,               # 70,000 cr
            "Net PPE": 5.0e11,                       # 50,000 cr
            "Total Assets": 1.5e12,                  # 150,000 cr
            "Current Liabilities": 4.0e11,           # 40,000 cr
            "Current Debt": 5.0e10,                  # 5,000 cr
            "Long Term Debt": 2.0e11,                # 20,000 cr
            "Total Liabilities Net Minority Interest": 6.0e11,  # 60,000 cr
            "Common Stock": 1.0e10,                  # 1,000 cr
            "Retained Earnings": 8.9e11,             # 89,000 cr
            "Stockholders Equity": 9.0e11,           # 90,000 cr
        },
        "cashflow": {
            "Operating Cash Flow": 3.0e11,   # 30,000 cr
            "Capital Expenditure": -8.0e10,  # reported negative -> 8,000 cr magnitude
            "Free Cash Flow": 2.2e11,        # 22,000 cr
            "Cash Dividends Paid": -5.0e10,  # reported negative -> 5,000 cr magnitude
        },
    }


def _healthy_payload() -> dict:
    return {
        "source": "yfinance",
        "symbol": "TEST.NS",
        "currency": "INR",
        "as_of": "2026-09-16T12:00:00+00:00",
        "profile": {"long_name": "Test Industries Ltd", "sector": "Capital Goods",
                    "industry": "Industrial Machinery"},
        "annual": [
            _annual_period("2026-03-31"),
            _annual_period("2025-03-31"),
            _annual_period("2024-03-31"),
        ],
        "quarterly": [_annual_period("2026-06-30")],
    }


# --- field mapping and units -----------------------------------------------

def test_revenue_and_pat_converted_to_crore():
    stmts = normalise_statements(_healthy_payload())
    latest = stmts.latest
    assert latest.income.revenue == pytest.approx(200_000.0)
    assert latest.income.pat == pytest.approx(27_000.0)


def test_gross_profit_is_derived_not_invented():
    # cogs is provided; gross_profit is not — the model derives it arithmetically.
    stmts = normalise_statements(_healthy_payload())
    assert stmts.latest.income.gross_profit == pytest.approx(80_000.0)


def test_share_count_scaled_to_crore_shares_gives_rupee_eps():
    stmts = normalise_statements(_healthy_payload())
    latest = stmts.latest
    assert latest.income.shares_diluted == pytest.approx(1_000.0)
    assert latest.income.eps_diluted == pytest.approx(27.0)  # 27,000 cr / 1,000 cr


def test_capex_sign_normalised_to_positive_magnitude():
    stmts = normalise_statements(_healthy_payload())
    assert stmts.latest.cashflow.capex == pytest.approx(8_000.0)
    assert stmts.latest.cashflow.capex > 0


def test_dividends_paid_sign_normalised_to_positive_magnitude():
    stmts = normalise_statements(_healthy_payload())
    assert stmts.latest.cashflow.dividends_paid == pytest.approx(5_000.0)


def test_missing_field_stays_none_rather_than_synthesised():
    payload = _healthy_payload()
    for period in payload["annual"]:
        period["income"].pop("EBITDA")
        period["income"].pop("Operating Income")
        period["income"].pop("Reconciled Depreciation")
    stmts = normalise_statements(payload)
    # With no EBITDA, no EBIT and no depreciation, none can be derived: they
    # must remain None, not be back-filled.
    assert stmts.latest.income.ebitda is None
    assert stmts.latest.income.ebit is None


# --- fiscal-year labelling and ordering ------------------------------------

def test_fiscal_year_label_follows_period_end_year():
    stmts = normalise_statements(_healthy_payload())
    assert stmts.latest.label == "FY2026"
    assert [p.label for p in stmts.annual] == ["FY2026", "FY2025", "FY2024"]


def test_annual_periods_sorted_newest_first():
    payload = _healthy_payload()
    payload["annual"] = list(reversed(payload["annual"]))  # feed oldest-first
    stmts = normalise_statements(payload)
    assert stmts.latest.label == "FY2026"


def test_quarterly_periods_flagged_as_quarterly():
    from app.models.statements import PeriodType

    stmts = normalise_statements(_healthy_payload())
    assert stmts.quarterly
    assert all(p.period_type is PeriodType.QUARTERLY for p in stmts.quarterly)


def test_identity_hints_override_provider_profile():
    stmts = normalise_statements(
        _healthy_payload(), ticker="TCS.NS", company_name="Tata Consultancy",
        sector="IT Services",
    )
    assert stmts.ticker == "TCS.NS"
    assert stmts.company_name == "Tata Consultancy"
    assert stmts.sector == "IT Services"


# --- data quality ----------------------------------------------------------

def test_complete_statements_are_sufficient_to_rate():
    stmts = normalise_statements(_healthy_payload())
    dq = build_data_quality(stmts, sources_used=["yfinance"])
    assert dq.completeness_pct == pytest.approx(100.0)
    assert dq.annual_periods_available == 3
    assert dq.missing_fields == []
    assert dq.sufficient_to_rate is True
    assert dq.sources_used == ["yfinance"]


def test_thin_data_falls_below_v1_threshold():
    payload = {
        "source": "yfinance",
        "symbol": "THIN.NS",
        "currency": "INR",
        "annual": [
            {"period_end": "2026-03-31",
             "income": {"Total Revenue": 1.0e11, "Net Income": 1.0e10},
             "balance": {}, "cashflow": {}},
        ],
    }
    stmts = normalise_statements(payload)
    dq = build_data_quality(stmts, sources_used=["yfinance"])
    assert dq.completeness_pct < 70.0
    assert dq.sufficient_to_rate is False
    assert any("veto V1" in w for w in dq.warnings)


def test_balance_sheet_that_does_not_balance_is_flagged():
    payload = _healthy_payload()
    # Break the latest period's balance: assets no longer equal L + E.
    payload["annual"][0]["balance"]["Total Assets"] = 2.0e12  # 200,000 cr, was 150,000
    stmts = normalise_statements(payload)
    dq = build_data_quality(stmts)
    assert any("FY2026" in f for f in dq.balance_check_failures)


def test_no_annual_periods_yields_zero_completeness():
    stmts = normalise_statements({"source": "yfinance", "symbol": "X", "annual": []})
    dq = build_data_quality(stmts)
    assert dq.completeness_pct == 0.0
    assert dq.annual_periods_available == 0
    assert dq.sufficient_to_rate is False


def test_normalisation_does_not_mutate_the_raw_payload():
    payload = _healthy_payload()
    snapshot = copy.deepcopy(payload)
    normalise_statements(payload)
    assert payload == snapshot


# --- market data -----------------------------------------------------------

def _market_payload() -> dict:
    return {
        "source": "yfinance",
        "symbol": "TEST.NS",
        "currency": "INR",
        "as_of": "2026-09-16T12:00:00+00:00",
        "quote": {
            "current_price": 3500.0,
            "market_cap": 3.5e13,          # 3,500,000 cr
            "shares_outstanding": 1.0e10,  # 1,000 cr shares
            "beta": 0.85,
            "week_52_high": 4200.0,
            "week_52_low": 2900.0,
            "avg_daily_volume": 1_500_000,
        },
        "price_history": [["2021-09-16", 3000.0], ["2026-09-16", 3500.0]],
    }


def test_market_price_kept_and_cap_converted_to_crore():
    market = normalise_market(_market_payload())
    assert market.cmp == pytest.approx(3500.0)
    assert market.market_cap == pytest.approx(3_500_000.0)
    assert market.shares_outstanding == pytest.approx(1_000.0)


def test_market_price_history_parsed():
    market = normalise_market(_market_payload())
    assert market.price_history[0] == ("2021-09-16", 3000.0)
    assert len(market.price_history) == 2


def test_market_without_price_is_an_error():
    payload = _market_payload()
    payload["quote"].pop("current_price")
    with pytest.raises(ValueError):
        normalise_market(payload)


def test_crore_constant_is_ten_million():
    assert CRORE == 1e7
