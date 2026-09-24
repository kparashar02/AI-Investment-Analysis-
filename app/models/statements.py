"""Canonical financial statement models.

Every provider (FMP, yfinance, a manual CSV) is normalised into these shapes
before anything downstream touches the numbers. The engine only ever sees
this schema, which is what makes the provider layer swappable.

Conventions, applied without exception:

* **Units are rupees crore** for every monetary field. Share counts are in
  crore of shares, so ``pat / shares_diluted`` yields EPS in rupees directly.
* **Signs are natural**: costs, capex, depreciation, interest and tax are
  stored as positive magnitudes. Providers that return negative expenses are
  corrected in the normalisation layer, not here.
* **Fiscal years follow the Indian convention.** ``label`` is the fiscal year
  the period *ends* in, so FY2026 for an Indian company ordinarily means the
  twelve months ending 31 March 2026. This is recorded explicitly because
  provider labelling of Indian fiscal years is inconsistent (PRD R11).
* **Consolidated is preferred** over standalone. The basis actually used is
  recorded on every period so the report can disclose it (PRD R12).
"""

from __future__ import annotations

from enum import Enum
from typing import Any

from pydantic import BaseModel, Field, model_validator


class ReportingBasis(str, Enum):
    CONSOLIDATED = "CONSOLIDATED"
    STANDALONE = "STANDALONE"
    UNKNOWN = "UNKNOWN"


class PeriodType(str, Enum):
    ANNUAL = "ANNUAL"
    QUARTERLY = "QUARTERLY"


class IncomeStatement(BaseModel):
    """Profit & loss for one period, in rupees crore."""

    revenue: float
    cogs: float | None = None
    gross_profit: float | None = None
    other_operating_expenses: float | None = None
    ebitda: float | None = None
    depreciation_amortisation: float | None = None
    ebit: float | None = None
    other_income: float | None = None
    interest_expense: float | None = None
    pbt: float | None = None
    tax_expense: float | None = None
    pat: float

    shares_basic: float | None = None
    shares_diluted: float | None = None
    dps: float | None = None

    @model_validator(mode="after")
    def _derive(self) -> IncomeStatement:
        """Fill gaps that follow arithmetically from what the provider gave us.

        Only unambiguous single-step derivations are performed. Anything that
        would require an assumption is left as ``None`` so that the data
        quality report can count it as missing rather than quietly inventing
        a value.
        """
        if self.gross_profit is None and self.cogs is not None:
            self.gross_profit = self.revenue - self.cogs
        elif self.cogs is None and self.gross_profit is not None:
            self.cogs = self.revenue - self.gross_profit

        if self.ebitda is None and self.ebit is not None and self.depreciation_amortisation is not None:
            self.ebitda = self.ebit + self.depreciation_amortisation
        elif (
            self.ebitda is None
            and self.gross_profit is not None
            and self.other_operating_expenses is not None
        ):
            self.ebitda = self.gross_profit - self.other_operating_expenses

        if self.ebit is None and self.ebitda is not None and self.depreciation_amortisation is not None:
            self.ebit = self.ebitda - self.depreciation_amortisation

        if self.pbt is None and self.tax_expense is not None:
            self.pbt = self.pat + self.tax_expense
        elif self.tax_expense is None and self.pbt is not None:
            self.tax_expense = self.pbt - self.pat

        return self

    @property
    def effective_tax_rate(self) -> float | None:
        """Tax expense over PBT, as a fraction. ``None`` if PBT is non-positive."""
        if self.pbt is None or self.pbt <= 0 or self.tax_expense is None:
            return None
        return self.tax_expense / self.pbt

    @property
    def eps_diluted(self) -> float | None:
        shares = self.shares_diluted or self.shares_basic
        if not shares:
            return None
        return self.pat / shares


class BalanceSheet(BaseModel):
    """Position at period end, in rupees crore."""

    cash_and_equivalents: float | None = None
    current_investments: float | None = None
    inventory: float | None = None
    receivables: float | None = None
    other_current_assets: float | None = None
    total_current_assets: float | None = None

    net_fixed_assets: float | None = None
    goodwill_and_intangibles: float | None = None
    other_non_current_assets: float | None = None
    total_assets: float | None = None

    payables: float | None = None
    short_term_debt: float | None = None
    other_current_liabilities: float | None = None
    total_current_liabilities: float | None = None

    long_term_debt: float | None = None
    other_non_current_liabilities: float | None = None
    total_liabilities: float | None = None

    share_capital: float | None = None
    reserves: float | None = None
    retained_earnings: float | None = None
    shareholders_equity: float | None = None
    minority_interest: float | None = None

    @model_validator(mode="after")
    def _derive(self) -> BalanceSheet:
        if self.total_current_assets is None:
            parts = [
                self.cash_and_equivalents,
                self.current_investments,
                self.inventory,
                self.receivables,
                self.other_current_assets,
            ]
            if any(p is not None for p in parts):
                self.total_current_assets = sum(p for p in parts if p is not None)

        if self.total_current_liabilities is None:
            parts = [self.payables, self.short_term_debt, self.other_current_liabilities]
            if any(p is not None for p in parts):
                self.total_current_liabilities = sum(p for p in parts if p is not None)

        if self.shareholders_equity is None and self.share_capital is not None:
            reserves = self.reserves if self.reserves is not None else self.retained_earnings
            if reserves is not None:
                self.shareholders_equity = self.share_capital + reserves

        if self.total_assets is None:
            parts = [
                self.total_current_assets,
                self.net_fixed_assets,
                self.goodwill_and_intangibles,
                self.other_non_current_assets,
            ]
            if all(p is not None for p in parts[:2]):
                self.total_assets = sum(p for p in parts if p is not None)

        if self.total_liabilities is None:
            parts = [
                self.total_current_liabilities,
                self.long_term_debt,
                self.other_non_current_liabilities,
            ]
            if any(p is not None for p in parts):
                self.total_liabilities = sum(p for p in parts if p is not None)

        return self

    @property
    def total_debt(self) -> float | None:
        parts = [self.short_term_debt, self.long_term_debt]
        if all(p is None for p in parts):
            return None
        return sum(p for p in parts if p is not None)

    @property
    def net_debt(self) -> float | None:
        debt = self.total_debt
        if debt is None:
            return None
        cash = (self.cash_and_equivalents or 0.0) + (self.current_investments or 0.0)
        return debt - cash

    @property
    def net_working_capital(self) -> float | None:
        if self.total_current_assets is None or self.total_current_liabilities is None:
            return None
        return self.total_current_assets - self.total_current_liabilities

    @property
    def capital_employed(self) -> float | None:
        """Total assets less current liabilities — the ROCE denominator."""
        if self.total_assets is None or self.total_current_liabilities is None:
            return None
        return self.total_assets - self.total_current_liabilities

    def balance_check(self) -> tuple[bool, float | None]:
        """Assets = Liabilities + Equity, within tolerance.

        Returns ``(passed, deviation_pct)``. A statement that fails this is
        flagged loudly rather than silently used: a balance sheet that does
        not balance means the normalisation mapped a field wrongly, and every
        ratio built on it would be wrong too.
        """
        if self.total_assets in (None, 0) or self.total_liabilities is None:
            return (False, None)
        equity = (self.shareholders_equity or 0.0) + (self.minority_interest or 0.0)
        deviation = abs(self.total_assets - (self.total_liabilities + equity))
        pct = deviation / abs(self.total_assets) * 100.0
        return (pct <= 0.1, pct)


class CashFlowStatement(BaseModel):
    """Cash flows for one period, in rupees crore.

    ``capex`` is stored as a positive magnitude of cash spent.
    """

    operating_cash_flow: float | None = None
    capex: float | None = None
    free_cash_flow: float | None = None
    investing_cash_flow: float | None = None
    financing_cash_flow: float | None = None
    dividends_paid: float | None = None
    depreciation_amortisation: float | None = None
    change_in_working_capital: float | None = None
    net_change_in_cash: float | None = None

    @model_validator(mode="after")
    def _derive(self) -> CashFlowStatement:
        if (
            self.free_cash_flow is None
            and self.operating_cash_flow is not None
            and self.capex is not None
        ):
            self.free_cash_flow = self.operating_cash_flow - self.capex
        return self


class AnnualPeriod(BaseModel):
    """One fiscal year: the three statements plus provenance."""

    label: str = Field(description="Fiscal year of period END, e.g. 'FY2026'")
    period_end: str = Field(description="ISO date, e.g. '2026-03-31'")
    period_type: PeriodType = PeriodType.ANNUAL
    basis: ReportingBasis = ReportingBasis.UNKNOWN
    currency: str = "INR"
    unit: str = "CRORE"

    income: IncomeStatement
    balance: BalanceSheet
    cashflow: CashFlowStatement

    source: str = "unknown"
    derived_fields: list[str] = Field(default_factory=list)

    @property
    def fiscal_year(self) -> int:
        """Numeric year parsed from the label, for ordering."""
        digits = "".join(ch for ch in self.label if ch.isdigit())
        return int(digits) if digits else 0


class DataQualityReport(BaseModel):
    """What we actually got, and what we did not.

    This drives veto rule V1: below ``min_completeness_pct_to_rate`` the
    system declines to issue a rating instead of rating on thin data.
    """

    completeness_pct: float
    annual_periods_available: int
    quarterly_periods_available: int = 0
    missing_fields: list[str] = Field(default_factory=list)
    balance_check_failures: list[str] = Field(default_factory=list)
    mixed_reporting_basis: bool = False
    sources_used: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)

    @property
    def sufficient_to_rate(self) -> bool:
        return self.completeness_pct >= 70.0 and self.annual_periods_available >= 3


class FinancialStatements(BaseModel):
    """The full normalised statement history for one company.

    ``annual`` is held newest-first. This is enforced on construction so that
    ``annual[0]`` is unambiguously the latest reported year everywhere in the
    engine — an off-by-one here would silently corrupt every trend metric.
    """

    ticker: str
    company_name: str
    sector: str | None = None
    industry: str | None = None
    fiscal_year_end: str = "03-31"
    annual: list[AnnualPeriod]
    quarterly: list[AnnualPeriod] = Field(default_factory=list)
    data_quality: DataQualityReport | None = None

    @model_validator(mode="after")
    def _sort_and_check(self) -> FinancialStatements:
        self.annual.sort(key=lambda p: p.fiscal_year, reverse=True)
        self.quarterly.sort(key=lambda p: p.period_end, reverse=True)

        bases = {p.basis for p in self.annual if p.basis is not ReportingBasis.UNKNOWN}
        if len(bases) > 1 and self.data_quality is not None:
            self.data_quality.mixed_reporting_basis = True
            self.data_quality.warnings.append(
                "Reporting basis changes across years "
                f"({', '.join(sorted(b.value for b in bases))}); ratio trends "
                "are not strictly comparable."
            )
        return self

    @property
    def latest(self) -> AnnualPeriod:
        return self.annual[0]

    @property
    def previous(self) -> AnnualPeriod | None:
        return self.annual[1] if len(self.annual) > 1 else None

    def series(self, extractor: Any, oldest_first: bool = True) -> list[float | None]:
        """Pull one field across every annual period.

        ``extractor`` is a callable taking an :class:`AnnualPeriod`. Returns
        oldest-first by default, which is the order every time-series helper
        in :mod:`app.engine.helpers` expects.
        """
        values = [extractor(p) for p in self.annual]
        return list(reversed(values)) if oldest_first else values


class MarketData(BaseModel):
    """Price and market-derived figures. Separate from statements because it
    has a completely different staleness profile — quotes expire in minutes,
    statements in months."""

    ticker: str
    cmp: float = Field(description="Current market price, rupees per share")
    market_cap: float | None = Field(default=None, description="Rupees crore")
    shares_outstanding: float | None = Field(default=None, description="Crore shares")
    beta: float | None = None
    week_52_high: float | None = None
    week_52_low: float | None = None
    avg_daily_volume: float | None = None
    price_history: list[tuple[str, float]] = Field(default_factory=list)
    as_of: str | None = None
    source: str = "unknown"

    @model_validator(mode="after")
    def _derive(self) -> MarketData:
        if self.market_cap is None and self.shares_outstanding is not None:
            self.market_cap = self.cmp * self.shares_outstanding
        elif self.shares_outstanding is None and self.market_cap is not None and self.cmp:
            self.shares_outstanding = self.market_cap / self.cmp
        return self

    def enterprise_value(self, balance: BalanceSheet) -> float | None:
        """Market cap plus net debt, in rupees crore."""
        if self.market_cap is None:
            return None
        net_debt = balance.net_debt
        if net_debt is None:
            return None
        return self.market_cap + net_debt
