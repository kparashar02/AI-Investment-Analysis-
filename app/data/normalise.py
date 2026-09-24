"""Provider payload → canonical schema (PRD 8.3, 9.3).

This is the only place that knows a given source's field vocabulary, its units
and its sign conventions. Everything downstream sees the canonical models in
:mod:`app.models.statements` and nothing else, which is what makes the source
swappable and the engine source-agnostic.

Three normalisations happen here, and only here:

* **Field mapping** — a source's own line-item label (yfinance's "Total
  Revenue") onto the canonical field (``revenue``).
* **Units** — absolute reporting-currency amounts to rupees **crore** (÷1e7),
  and absolute share counts to **crore of shares**, so that ``pat / shares``
  is EPS in rupees. Per-share prices are left untouched.
* **Signs** — magnitudes that a source reports as negative outflows (capex,
  dividends paid) are stored as the positive magnitudes the models require.

Two rules are absolute, both from the PRD:

1. **Never synthesise a missing line** (PRD 8.3). A field the source did not
   provide stays ``None`` so the data-quality report can count it as missing,
   rather than being back-filled with a guess. The only gap-filling permitted
   is the unambiguous single-step arithmetic already inside the models
   (e.g. ``gross_profit = revenue - cogs``), which is derivation, not invention.
2. **A balance sheet that does not balance is flagged, not used quietly**
   (PRD 8.3 validation) — surfaced through ``DataQualityReport``.
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from app.models.statements import (
    AnnualPeriod,
    BalanceSheet,
    CashFlowStatement,
    DataQualityReport,
    FinancialStatements,
    IncomeStatement,
    MarketData,
    PeriodType,
    ReportingBasis,
)

CRORE = 1e7  # 1 crore = 10,000,000 — absolute rupees per crore, and shares per crore-share

# --- yfinance field maps ---------------------------------------------------
# canonical field -> tuple of candidate yfinance labels, tried in order. The
# first present, non-null candidate wins. Multiple candidates absorb yfinance's
# labelling drift across versions and across companies (PRD R11).

_YF_INCOME: dict[str, tuple[str, ...]] = {
    "revenue": ("Total Revenue", "Operating Revenue"),
    "cogs": ("Cost Of Revenue",),
    "gross_profit": ("Gross Profit",),
    "other_operating_expenses": ("Operating Expense",),
    "ebitda": ("EBITDA", "Normalized EBITDA"),
    "depreciation_amortisation": (
        "Reconciled Depreciation",
        "Depreciation And Amortization In Income Statement",
        "Depreciation Amortization Depletion Income Statement",
    ),
    "ebit": ("EBIT", "Operating Income", "Total Operating Income As Reported"),
    "other_income": ("Other Income Expense", "Net Non Operating Interest Income Expense"),
    "interest_expense": ("Interest Expense", "Interest Expense Non Operating"),
    "pbt": ("Pretax Income",),
    "tax_expense": ("Tax Provision",),
    "pat": ("Net Income", "Net Income Common Stockholders", "Net Income From Continuing Operations"),
    "shares_basic": ("Basic Average Shares",),
    "shares_diluted": ("Diluted Average Shares",),
}

_YF_BALANCE: dict[str, tuple[str, ...]] = {
    "cash_and_equivalents": ("Cash And Cash Equivalents", "Cash Cash Equivalents And Short Term Investments"),
    "current_investments": ("Other Short Term Investments",),
    "inventory": ("Inventory",),
    "receivables": ("Receivables", "Accounts Receivable"),
    "other_current_assets": ("Other Current Assets",),
    "total_current_assets": ("Current Assets", "Total Current Assets"),
    "net_fixed_assets": ("Net PPE", "Net Property Plant And Equipment"),
    "goodwill_and_intangibles": ("Goodwill And Other Intangible Assets", "Goodwill"),
    "other_non_current_assets": ("Other Non Current Assets",),
    "total_assets": ("Total Assets",),
    "payables": ("Payables", "Accounts Payable", "Payables And Accrued Expenses"),
    "short_term_debt": ("Current Debt", "Current Debt And Capital Lease Obligation"),
    "other_current_liabilities": ("Other Current Liabilities",),
    "total_current_liabilities": ("Current Liabilities", "Total Current Liabilities"),
    "long_term_debt": ("Long Term Debt", "Long Term Debt And Capital Lease Obligation"),
    "other_non_current_liabilities": ("Other Non Current Liabilities",),
    "total_liabilities": ("Total Liabilities Net Minority Interest", "Total Liabilities"),
    "share_capital": ("Common Stock", "Capital Stock"),
    "reserves": ("Additional Paid In Capital",),
    "retained_earnings": ("Retained Earnings",),
    "shareholders_equity": ("Stockholders Equity", "Common Stock Equity", "Total Equity Gross Minority Interest"),
    "minority_interest": ("Minority Interest",),
}

_YF_CASHFLOW: dict[str, tuple[str, ...]] = {
    "operating_cash_flow": ("Operating Cash Flow", "Cash Flow From Continuing Operating Activities"),
    "capex": ("Capital Expenditure", "Purchase Of PPE"),
    "free_cash_flow": ("Free Cash Flow",),
    "investing_cash_flow": ("Investing Cash Flow", "Cash Flow From Continuing Investing Activities"),
    "financing_cash_flow": ("Financing Cash Flow", "Cash Flow From Continuing Financing Activities"),
    "dividends_paid": ("Cash Dividends Paid", "Common Stock Dividend Paid"),
    "depreciation_amortisation": ("Depreciation And Amortization", "Depreciation Amortization Depletion"),
    "change_in_working_capital": ("Change In Working Capital",),
    "net_change_in_cash": ("Changes In Cash", "End Cash Position"),
}

# Fields the source reports as a negative outflow that the models store as a
# positive magnitude of cash spent.
_ABS_MAGNITUDE = {"capex", "dividends_paid"}

# Fields that are share counts (converted to crore-shares), not rupee amounts.
_SHARE_COUNTS = {"shares_basic", "shares_diluted"}

# The core fields the scoring engine actually needs. Completeness (PRD 9.3, and
# veto V1) is measured against this set on the latest annual period — present
# after the models' own arithmetic derivations have run, which is the honest
# "what we ended up with".
CORE_FIELDS: tuple[tuple[str, str], ...] = (
    ("income", "revenue"),
    ("income", "pat"),
    ("income", "ebitda"),
    ("income", "ebit"),
    ("income", "interest_expense"),
    ("income", "pbt"),
    ("income", "tax_expense"),
    ("income", "depreciation_amortisation"),
    ("income", "shares_diluted"),
    ("balance", "total_assets"),
    ("balance", "total_current_assets"),
    ("balance", "total_current_liabilities"),
    ("balance", "shareholders_equity"),
    ("balance", "short_term_debt"),
    ("balance", "long_term_debt"),
    ("balance", "cash_and_equivalents"),
    ("balance", "inventory"),
    ("balance", "receivables"),
    ("balance", "net_fixed_assets"),
    ("balance", "total_liabilities"),
    ("cashflow", "operating_cash_flow"),
    ("cashflow", "capex"),
    ("cashflow", "free_cash_flow"),
)


def _pick(raw: dict[str, Any], candidates: tuple[str, ...]) -> float | None:
    """First present, finite candidate label from a raw statement map."""
    for label in candidates:
        if label in raw and raw[label] is not None:
            try:
                value = float(raw[label])
            except (TypeError, ValueError):
                continue
            if value != value:  # NaN
                continue
            return value
    return None


def _to_crore(value: float | None, field: str) -> float | None:
    """Absolute source units → crore, with sign correction for outflows."""
    if value is None:
        return None
    scaled = value / CRORE
    if field in _ABS_MAGNITUDE:
        return abs(scaled)
    return scaled


def _map_statement(
    raw: dict[str, Any], field_map: dict[str, tuple[str, ...]]
) -> dict[str, float | None]:
    """Apply one statement's field map, converting units and share counts."""
    out: dict[str, float | None] = {}
    for field, candidates in field_map.items():
        value = _pick(raw, candidates)
        if field in _SHARE_COUNTS:
            out[field] = None if value is None else value / CRORE
        else:
            out[field] = _to_crore(value, field)
    return out


def _fy_label(period_end: str) -> str:
    """Fiscal-year label from a period-end date.

    The Indian convention labels a year by the calendar year it *ends* in, so a
    period ending 2026-03-31 is FY2026 (PRD model convention; R11). Using the
    year of the period end also does the right thing for a December-year
    company without special-casing.
    """
    try:
        year = datetime.fromisoformat(period_end).year
    except ValueError:
        digits = "".join(ch for ch in period_end if ch.isdigit())[:4]
        year = int(digits) if len(digits) == 4 else 0
    return f"FY{year}"


def _period(raw_period: dict[str, Any], source: str, basis: ReportingBasis) -> AnnualPeriod:
    income = IncomeStatement(**_map_statement(raw_period.get("income") or {}, _YF_INCOME))
    balance = BalanceSheet(**_map_statement(raw_period.get("balance") or {}, _YF_BALANCE))
    cashflow = CashFlowStatement(**_map_statement(raw_period.get("cashflow") or {}, _YF_CASHFLOW))
    period_end = str(raw_period.get("period_end", ""))
    return AnnualPeriod(
        label=_fy_label(period_end),
        period_end=period_end,
        basis=basis,
        source=source,
        income=income,
        balance=balance,
        cashflow=cashflow,
    )


def _build_periods(raw_periods: list, source: str, basis: ReportingBasis) -> list[AnnualPeriod]:
    """Build periods, skipping any that cannot form a valid statement.

    A provider (yfinance especially) can return a period-end with a balance
    sheet but no income line — e.g. a partial, rate-limited response. Such a
    period cannot build an :class:`IncomeStatement` (revenue/PAT are required);
    rather than crash the whole analysis, it is dropped, and the data-quality
    report reflects the reduced coverage."""
    from pydantic import ValidationError  # noqa: PLC0415

    periods: list[AnnualPeriod] = []
    for raw_period in raw_periods:
        try:
            periods.append(_period(raw_period, source, basis))
        except ValidationError:
            continue
    return periods


def normalise_statements(
    raw: dict[str, Any],
    *,
    ticker: str | None = None,
    company_name: str | None = None,
    sector: str | None = None,
    industry: str | None = None,
) -> FinancialStatements:
    """Raw statements payload → canonical :class:`FinancialStatements`.

    Explicit ``ticker``/``company_name``/``sector``/``industry`` overrides win
    over the payload's own ``profile`` block, so a caller that already resolved
    a company identity (Phase 3) is authoritative over the provider's guess.
    The result carries no ``data_quality`` yet — call :func:`build_data_quality`
    once the statements exist, so the report can inspect the canonical numbers.
    """
    profile = raw.get("profile") or {}
    source = str(raw.get("source", "unknown"))

    # yfinance does not distinguish consolidated from standalone; the models
    # record UNKNOWN rather than assert a basis we cannot verify (PRD R12).
    basis = ReportingBasis.UNKNOWN

    annual = _build_periods(raw.get("annual") or [], source, basis)
    quarterly = _build_periods(raw.get("quarterly") or [], source, basis)
    for q in quarterly:
        q.period_type = PeriodType.QUARTERLY

    return FinancialStatements(
        ticker=(ticker or raw.get("symbol") or profile.get("symbol") or "UNKNOWN"),
        company_name=(company_name or profile.get("long_name") or ticker or "Unknown"),
        sector=(sector if sector is not None else profile.get("sector")),
        industry=(industry if industry is not None else profile.get("industry")),
        annual=annual,
        quarterly=quarterly,
    )


def _field_present(period: AnnualPeriod, statement: str, field: str) -> bool:
    obj = getattr(period, {"income": "income", "balance": "balance", "cashflow": "cashflow"}[statement])
    return getattr(obj, field, None) is not None


def build_data_quality(
    statements: FinancialStatements,
    *,
    sources_used: list[str] | None = None,
    extra_warnings: list[str] | None = None,
) -> DataQualityReport:
    """Measure what actually arrived (PRD 9.3).

    Completeness is the fraction of :data:`CORE_FIELDS` present on the latest
    annual period. Below the model's ``sufficient_to_rate`` threshold, veto V1
    later declines to issue a rating rather than rate on thin data — so this
    number is load-bearing, not cosmetic.
    """
    annual = statements.annual
    warnings: list[str] = list(extra_warnings or [])

    if not annual:
        return DataQualityReport(
            completeness_pct=0.0,
            annual_periods_available=0,
            quarterly_periods_available=len(statements.quarterly),
            missing_fields=[f"{s}.{f}" for s, f in CORE_FIELDS],
            sources_used=sources_used or [],
            warnings=warnings + ["No annual periods were returned by any source."],
        )

    latest = annual[0]
    missing: list[str] = []
    present = 0
    for statement, field in CORE_FIELDS:
        if _field_present(latest, statement, field):
            present += 1
        else:
            missing.append(f"{statement}.{field}")
    completeness = present / len(CORE_FIELDS) * 100.0

    balance_failures: list[str] = []
    for period in annual:
        passed, deviation = period.balance.balance_check()
        if not passed and deviation is not None:
            balance_failures.append(
                f"{period.label} (deviation {deviation:.2f}% of total assets)"
            )

    report = DataQualityReport(
        completeness_pct=round(completeness, 2),
        annual_periods_available=len(annual),
        quarterly_periods_available=len(statements.quarterly),
        missing_fields=missing,
        balance_check_failures=balance_failures,
        sources_used=sources_used or [],
        warnings=warnings,
    )
    if not report.sufficient_to_rate:
        report.warnings.append(
            f"Data completeness {report.completeness_pct:.0f}% over "
            f"{report.annual_periods_available} annual period(s) is below the "
            f"rating threshold; veto V1 applies and no rating will be issued."
        )
    return report


def normalise_market(
    raw: dict[str, Any],
    *,
    ticker: str | None = None,
) -> MarketData:
    """Raw market payload → canonical :class:`MarketData`.

    Market cap is an absolute rupee amount → crore; shares outstanding an
    absolute count → crore-shares; the current price is per-share and left as
    is. ``as_of`` defaults to now (UTC) if the payload did not stamp it.
    """
    quote = raw.get("quote") or {}
    price = quote.get("current_price")
    if price is None:
        raise ValueError("market payload has no current price")

    def crore(key: str) -> float | None:
        v = quote.get(key)
        return None if v is None else float(v) / CRORE

    history: list[tuple[str, float]] = []
    for point in raw.get("price_history") or []:
        try:
            history.append((str(point[0]), float(point[1])))
        except (TypeError, ValueError, IndexError):
            continue

    return MarketData(
        ticker=(ticker or raw.get("symbol") or "UNKNOWN"),
        cmp=float(price),
        market_cap=crore("market_cap"),
        shares_outstanding=crore("shares_outstanding"),
        beta=quote.get("beta"),
        week_52_high=quote.get("week_52_high"),
        week_52_low=quote.get("week_52_low"),
        avg_daily_volume=quote.get("avg_daily_volume"),
        price_history=history,
        as_of=str(raw.get("as_of") or datetime.now(timezone.utc).isoformat()),
        source=str(raw.get("source", "unknown")),
    )
