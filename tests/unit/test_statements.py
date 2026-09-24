"""Statement normalisation: derivation, validation and ordering.

The normalisation layer is where provider quirks are supposed to die. These
tests cover the three failure modes that would corrupt everything downstream:
a derived figure computed wrongly, a balance sheet that does not balance
passing unnoticed, and periods ending up in the wrong order so that
``latest`` is not actually the latest.
"""

from __future__ import annotations

import pytest

from app.models.statements import (
    AnnualPeriod,
    BalanceSheet,
    CashFlowStatement,
    FinancialStatements,
    IncomeStatement,
    MarketData,
    ReportingBasis,
)


# ------------------------------------------------------------- derivations ---
class TestIncomeStatementDerivation:
    def test_gross_profit_from_cogs(self):
        inc = IncomeStatement(revenue=1000.0, cogs=600.0, pat=100.0)
        assert inc.gross_profit == pytest.approx(400.0)

    def test_cogs_from_gross_profit(self):
        inc = IncomeStatement(revenue=1000.0, gross_profit=400.0, pat=100.0)
        assert inc.cogs == pytest.approx(600.0)

    def test_ebitda_from_ebit_and_depreciation(self):
        inc = IncomeStatement(
            revenue=1000.0, ebit=200.0, depreciation_amortisation=50.0, pat=100.0
        )
        assert inc.ebitda == pytest.approx(250.0)

    def test_ebitda_from_gross_profit_less_operating_expenses(self):
        inc = IncomeStatement(
            revenue=1000.0, cogs=600.0, other_operating_expenses=150.0, pat=100.0
        )
        assert inc.ebitda == pytest.approx(250.0)

    def test_ebit_from_ebitda_and_depreciation(self):
        inc = IncomeStatement(
            revenue=1000.0, ebitda=250.0, depreciation_amortisation=50.0, pat=100.0
        )
        assert inc.ebit == pytest.approx(200.0)

    def test_pbt_from_pat_and_tax(self):
        inc = IncomeStatement(revenue=1000.0, pat=100.0, tax_expense=30.0)
        assert inc.pbt == pytest.approx(130.0)

    def test_tax_from_pbt_and_pat(self):
        inc = IncomeStatement(revenue=1000.0, pat=100.0, pbt=130.0)
        assert inc.tax_expense == pytest.approx(30.0)

    def test_nothing_is_invented_when_inputs_are_absent(self):
        """A gap must stay a gap. This is what the data-quality report counts."""
        inc = IncomeStatement(revenue=1000.0, pat=100.0)
        assert inc.gross_profit is None
        assert inc.ebitda is None
        assert inc.ebit is None
        assert inc.pbt is None

    def test_effective_tax_rate_needs_positive_pbt(self):
        assert IncomeStatement(
            revenue=1000.0, pat=100.0, pbt=130.0, tax_expense=30.0
        ).effective_tax_rate == pytest.approx(30.0 / 130.0)
        # A loss-making year has no meaningful effective rate.
        assert IncomeStatement(
            revenue=1000.0, pat=-100.0, pbt=-130.0, tax_expense=0.0
        ).effective_tax_rate is None

    def test_eps_prefers_diluted_shares(self):
        inc = IncomeStatement(
            revenue=1000.0, pat=100.0, shares_basic=10.0, shares_diluted=12.5
        )
        assert inc.eps_diluted == pytest.approx(8.0)     # not 10.0


class TestBalanceSheetDerivation:
    def test_totals_are_summed_when_absent(self):
        bal = BalanceSheet(
            cash_and_equivalents=100.0, inventory=200.0, receivables=300.0,
            other_current_assets=50.0,
            net_fixed_assets=1000.0, other_non_current_assets=100.0,
            payables=150.0, short_term_debt=100.0, other_current_liabilities=50.0,
            long_term_debt=400.0, other_non_current_liabilities=100.0,
            share_capital=200.0, reserves=1000.0,
        )
        assert bal.total_current_assets == pytest.approx(650.0)
        assert bal.total_current_liabilities == pytest.approx(300.0)
        assert bal.shareholders_equity == pytest.approx(1200.0)
        assert bal.total_assets == pytest.approx(1750.0)
        assert bal.total_liabilities == pytest.approx(800.0)

    def test_debt_and_net_debt(self):
        bal = BalanceSheet(
            cash_and_equivalents=100.0, current_investments=50.0,
            short_term_debt=200.0, long_term_debt=500.0,
        )
        assert bal.total_debt == pytest.approx(700.0)
        # Liquid investments count toward cash when netting debt.
        assert bal.net_debt == pytest.approx(550.0)

    def test_net_cash_position_is_negative_net_debt(self):
        bal = BalanceSheet(cash_and_equivalents=1000.0, short_term_debt=100.0)
        assert bal.net_debt == pytest.approx(-900.0)

    def test_capital_employed(self):
        bal = BalanceSheet(total_assets=1000.0, total_current_liabilities=300.0)
        assert bal.capital_employed == pytest.approx(700.0)

    def test_missing_debt_gives_none_not_zero(self):
        assert BalanceSheet(cash_and_equivalents=100.0).total_debt is None
        assert BalanceSheet(cash_and_equivalents=100.0).net_debt is None


class TestBalanceCheck:
    def test_a_balanced_sheet_passes(self):
        bal = BalanceSheet(
            total_assets=1000.0, total_liabilities=600.0, shareholders_equity=400.0
        )
        passed, deviation = bal.balance_check()
        assert passed
        assert deviation == pytest.approx(0.0)

    def test_minority_interest_counts_toward_equity(self):
        bal = BalanceSheet(
            total_assets=1000.0, total_liabilities=600.0,
            shareholders_equity=350.0, minority_interest=50.0,
        )
        passed, _ = bal.balance_check()
        assert passed

    def test_an_unbalanced_sheet_fails_with_the_deviation(self):
        """This is a mapping error in the provider layer, not a rounding issue.

        Every ratio built on such a period is suspect, so it must surface.
        """
        bal = BalanceSheet(
            total_assets=1000.0, total_liabilities=600.0, shareholders_equity=300.0
        )
        passed, deviation = bal.balance_check()
        assert not passed
        assert deviation == pytest.approx(10.0)          # 100 / 1000

    def test_tolerance_absorbs_rounding(self):
        bal = BalanceSheet(
            total_assets=1000.0, total_liabilities=600.0, shareholders_equity=400.5
        )
        passed, deviation = bal.balance_check()
        assert passed                                     # 0.05% < 0.1%
        assert deviation == pytest.approx(0.05)


class TestCashFlowDerivation:
    def test_fcf_from_ocf_and_capex(self):
        cf = CashFlowStatement(operating_cash_flow=500.0, capex=200.0)
        assert cf.free_cash_flow == pytest.approx(300.0)

    def test_negative_fcf_is_preserved(self):
        cf = CashFlowStatement(operating_cash_flow=100.0, capex=400.0)
        assert cf.free_cash_flow == pytest.approx(-300.0)

    def test_no_derivation_without_capex(self):
        assert CashFlowStatement(operating_cash_flow=500.0).free_cash_flow is None


# ---------------------------------------------------------------- ordering ---
def _period(label: str, year_end: str) -> AnnualPeriod:
    return AnnualPeriod(
        label=label, period_end=year_end,
        income=IncomeStatement(revenue=100.0, pat=10.0),
        balance=BalanceSheet(total_assets=100.0, total_liabilities=60.0,
                             shareholders_equity=40.0),
        cashflow=CashFlowStatement(operating_cash_flow=12.0, capex=4.0),
    )


def test_annual_periods_are_sorted_newest_first():
    """``latest`` must be the latest regardless of the order a provider
    returned the periods in. An off-by-one here would silently invert every
    trend metric in the system."""
    statements = FinancialStatements(
        ticker="X.NS", company_name="X",
        annual=[
            _period("FY2023", "2023-03-31"),
            _period("FY2026", "2026-03-31"),
            _period("FY2024", "2024-03-31"),
            _period("FY2025", "2025-03-31"),
        ],
    )
    assert [p.label for p in statements.annual] == ["FY2026", "FY2025", "FY2024", "FY2023"]
    assert statements.latest.label == "FY2026"
    assert statements.previous.label == "FY2025"


def test_series_is_oldest_first():
    """The reverse of storage order, because every time-series helper —
    CAGR endpoints, OLS slope — expects chronological input."""
    statements = FinancialStatements(
        ticker="X.NS", company_name="X",
        annual=[_period("FY2025", "2025-03-31"), _period("FY2026", "2026-03-31")],
    )
    labels = [p.label for p in reversed(statements.annual)]
    assert labels == ["FY2025", "FY2026"]
    assert statements.series(lambda p: p.income.revenue) == [100.0, 100.0]


def test_previous_is_none_for_a_single_period():
    statements = FinancialStatements(
        ticker="X.NS", company_name="X", annual=[_period("FY2026", "2026-03-31")]
    )
    assert statements.previous is None


def test_acme_periods_are_in_order(acme):
    labels = [p.label for p in acme.statements.annual]
    assert labels == ["FY2026", "FY2025", "FY2024", "FY2023", "FY2022"]


def test_indian_fiscal_year_convention_is_recorded(acme):
    """FY2026 ends 31 March 2026, not 31 December.

    Provider labelling of Indian fiscal years is inconsistent (PRD R11), so
    the convention is held explicitly rather than inferred.
    """
    assert acme.statements.fiscal_year_end == "03-31"
    assert acme.statements.latest.period_end == "2026-03-31"
    assert acme.statements.latest.fiscal_year == 2026


def test_reporting_basis_is_recorded(acme):
    for period in acme.statements.annual:
        assert period.basis is ReportingBasis.CONSOLIDATED


# ------------------------------------------------------------ market data ----
def test_market_cap_derived_from_price_and_shares():
    market = MarketData(ticker="X.NS", cmp=150.0, shares_outstanding=100.0)
    assert market.market_cap == pytest.approx(15000.0)


def test_shares_derived_from_market_cap():
    market = MarketData(ticker="X.NS", cmp=150.0, market_cap=15000.0)
    assert market.shares_outstanding == pytest.approx(100.0)


def test_enterprise_value_adds_net_debt():
    market = MarketData(ticker="X.NS", cmp=150.0, shares_outstanding=100.0)
    balance = BalanceSheet(
        cash_and_equivalents=800.0, short_term_debt=400.0, long_term_debt=1600.0
    )
    assert market.enterprise_value(balance) == pytest.approx(16200.0)


def test_enterprise_value_needs_debt_information():
    market = MarketData(ticker="X.NS", cmp=150.0, shares_outstanding=100.0)
    assert market.enterprise_value(BalanceSheet(cash_and_equivalents=800.0)) is None
