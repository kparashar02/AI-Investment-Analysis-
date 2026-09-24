"""Ratio computation. Deterministic, no LLM, no exceptions to that.

This module is the arithmetic heart of the system. Everything a reader is
eventually told about a company's profitability, leverage, efficiency or
valuation is computed here, from normalised statements, in plain Python that
can be checked line by line against a textbook.

Two conventions that matter and are easy to get wrong:

* **Flow-over-stock ratios use average balance-sheet values.** ROE divides
  this year's profit by the *average* of opening and closing equity, because
  the profit was earned over the year, not at the instant of the closing
  balance sheet. Where no prior-year balance sheet exists the closing value
  is used and the metric records that fallback in its notes.
* **Ratios that are conceptually point-in-time use closing values** — ROCE,
  the leverage ratios and the liquidity ratios describe the position the
  company *ends* the year in. Each function below states which it uses.

DAYS_IN_YEAR is 365 throughout, not 360 or 366. Stated because working
capital day-counts move materially with the convention.
"""

from __future__ import annotations

from app.engine.helpers import average, pct, safe_div
from app.models.metrics import MetricValue, Pillar, Unit
from app.models.statements import AnnualPeriod, MarketData

DAYS_IN_YEAR = 365.0

# Fallback when a company's effective tax rate cannot be computed (typically
# a loss-making year, where tax/PBT is meaningless). India's headline rate
# for a domestic company opting into the concessional regime is 22% plus
# surcharge and cess, i.e. ~25.17%. Any metric using this fallback says so.
STATUTORY_TAX_RATE = 0.2517


def _m(
    name: str,
    label: str,
    value: float | None,
    unit: Unit,
    pillar: Pillar = Pillar.CONTEXT,
    formula: str = "",
    inputs: dict[str, float] | None = None,
    period: str | None = None,
    reason: str | None = None,
    notes: list[str] | None = None,
) -> MetricValue:
    """Construct a MetricValue, dropping ``None`` inputs from the audit trail."""
    clean = {k: v for k, v in (inputs or {}).items() if v is not None}
    return MetricValue(
        name=name,
        label=label,
        value=value,
        unit=unit,
        pillar=pillar,
        period=period,
        formula=formula,
        inputs_used=clean,
        unavailable_reason=reason if value is None else None,
        notes=notes or [],
    )


def _avg_bs(
    cur_value: float | None, prev_value: float | None
) -> tuple[float | None, list[str]]:
    """Two-point average of a balance-sheet item, with a provenance note."""
    if cur_value is None:
        return (None, [])
    if prev_value is None:
        return (cur_value, ["Prior-year balance sheet unavailable; closing position used."])
    return (average(cur_value, prev_value), [])


def _shares(period: AnnualPeriod) -> float | None:
    return period.income.shares_diluted or period.income.shares_basic


# ---------------------------------------------------------------------------
# Profitability — margins. Pure flow ratios, no balance-sheet input.
# ---------------------------------------------------------------------------
def profitability_ratios(cur: AnnualPeriod) -> dict[str, MetricValue]:
    inc = cur.income
    p = cur.label
    out: dict[str, MetricValue] = {}

    out["gross_margin"] = _m(
        "gross_margin", "Gross Margin",
        pct(inc.gross_profit, inc.revenue), Unit.PCT, Pillar.FUNDAMENTALS,
        "Gross Profit / Revenue",
        {"gross_profit": inc.gross_profit, "revenue": inc.revenue}, p,
        reason="gross profit not reported and COGS unavailable",
    )
    out["ebitda_margin"] = _m(
        "ebitda_margin", "EBITDA Margin",
        pct(inc.ebitda, inc.revenue), Unit.PCT, Pillar.FUNDAMENTALS,
        "EBITDA / Revenue",
        {"ebitda": inc.ebitda, "revenue": inc.revenue}, p,
        reason="EBITDA could not be derived",
    )
    out["ebit_margin"] = _m(
        "ebit_margin", "EBIT (Operating) Margin",
        pct(inc.ebit, inc.revenue), Unit.PCT, Pillar.FUNDAMENTALS,
        "EBIT / Revenue",
        {"ebit": inc.ebit, "revenue": inc.revenue}, p,
        reason="EBIT could not be derived",
    )
    out["net_margin"] = _m(
        "net_margin", "Net Profit Margin",
        pct(inc.pat, inc.revenue), Unit.PCT, Pillar.FUNDAMENTALS,
        "PAT / Revenue",
        {"pat": inc.pat, "revenue": inc.revenue}, p,
    )
    out["effective_tax_rate"] = _m(
        "effective_tax_rate", "Effective Tax Rate",
        pct(inc.tax_expense, inc.pbt) if (inc.pbt or 0) > 0 else None,
        Unit.PCT, Pillar.CONTEXT,
        "Tax Expense / PBT",
        {"tax_expense": inc.tax_expense, "pbt": inc.pbt}, p,
        reason="PBT non-positive; effective rate not meaningful",
    )
    return out


# ---------------------------------------------------------------------------
# Return ratios. ROE and ROA use average balance-sheet values; ROCE and ROIC
# use closing values (they describe the capital base in place at year end).
# ---------------------------------------------------------------------------
def return_ratios(
    cur: AnnualPeriod, prev: AnnualPeriod | None = None
) -> dict[str, MetricValue]:
    inc, bal = cur.income, cur.balance
    prev_bal = prev.balance if prev else None
    p = cur.label
    out: dict[str, MetricValue] = {}

    avg_equity, eq_notes = _avg_bs(
        bal.shareholders_equity, prev_bal.shareholders_equity if prev_bal else None
    )
    out["roe"] = _m(
        "roe", "Return on Equity",
        pct(inc.pat, avg_equity), Unit.PCT, Pillar.FUNDAMENTALS,
        "PAT / Average Shareholders' Equity",
        {"pat": inc.pat, "avg_equity": avg_equity}, p,
        reason="shareholders' equity unavailable", notes=eq_notes,
    )

    avg_assets, as_notes = _avg_bs(bal.total_assets, prev_bal.total_assets if prev_bal else None)
    out["roa"] = _m(
        "roa", "Return on Assets",
        pct(inc.pat, avg_assets), Unit.PCT, Pillar.FUNDAMENTALS,
        "PAT / Average Total Assets",
        {"pat": inc.pat, "avg_total_assets": avg_assets}, p,
        reason="total assets unavailable", notes=as_notes,
    )

    out["roce"] = _m(
        "roce", "Return on Capital Employed",
        pct(inc.ebit, bal.capital_employed), Unit.PCT, Pillar.FUNDAMENTALS,
        "EBIT / (Total Assets - Current Liabilities)",
        {
            "ebit": inc.ebit,
            "total_assets": bal.total_assets,
            "current_liabilities": bal.total_current_liabilities,
            "capital_employed": bal.capital_employed,
        }, p,
        reason="EBIT or capital employed unavailable",
        notes=["Closing capital employed, not average."],
    )

    # ROIC. NOPAT needs a tax rate; a loss-making year has no meaningful
    # effective rate, so the statutory rate stands in and the substitution is
    # disclosed on the metric rather than buried.
    eff_rate = inc.effective_tax_rate
    roic_notes: list[str] = ["Closing invested capital, not average."]
    if eff_rate is None or not (0.0 <= eff_rate <= 0.60):
        eff_rate = STATUTORY_TAX_RATE
        roic_notes.append(
            f"Effective tax rate unavailable or implausible; statutory "
            f"{STATUTORY_TAX_RATE * 100:.2f}% applied to derive NOPAT."
        )
    nopat = inc.ebit * (1.0 - eff_rate) if inc.ebit is not None else None
    invested_capital = None
    if bal.total_debt is not None and bal.shareholders_equity is not None:
        invested_capital = (
            bal.total_debt + bal.shareholders_equity - (bal.cash_and_equivalents or 0.0)
        )
    out["roic"] = _m(
        "roic", "Return on Invested Capital",
        pct(nopat, invested_capital), Unit.PCT, Pillar.FUNDAMENTALS,
        "NOPAT / (Total Debt + Equity - Cash)",
        {
            "nopat": nopat,
            "tax_rate_used": eff_rate,
            "invested_capital": invested_capital,
        }, p,
        reason="EBIT, debt or equity unavailable", notes=roic_notes,
    )
    return out


# ---------------------------------------------------------------------------
# Leverage and coverage. Closing values throughout.
# ---------------------------------------------------------------------------
def leverage_ratios(cur: AnnualPeriod) -> dict[str, MetricValue]:
    inc, bal = cur.income, cur.balance
    p = cur.label
    out: dict[str, MetricValue] = {}

    out["debt_to_equity"] = _m(
        "debt_to_equity", "Debt to Equity",
        safe_div(bal.total_debt, bal.shareholders_equity), Unit.RATIO, Pillar.FUNDAMENTALS,
        "Total Debt / Shareholders' Equity",
        {
            "total_debt": bal.total_debt,
            "short_term_debt": bal.short_term_debt,
            "long_term_debt": bal.long_term_debt,
            "shareholders_equity": bal.shareholders_equity,
        }, p,
        reason="debt or equity unavailable",
    )
    out["net_debt_to_ebitda"] = _m(
        "net_debt_to_ebitda", "Net Debt / EBITDA",
        safe_div(bal.net_debt, inc.ebitda), Unit.TIMES, Pillar.FUNDAMENTALS,
        "(Total Debt - Cash) / EBITDA",
        {"net_debt": bal.net_debt, "ebitda": inc.ebitda}, p,
        reason="net debt or EBITDA unavailable",
        notes=(
            ["Negative value indicates a net cash position."]
            if (bal.net_debt or 0) < 0 else []
        ),
    )
    out["interest_coverage"] = _m(
        "interest_coverage", "Interest Coverage",
        safe_div(inc.ebit, inc.interest_expense), Unit.TIMES, Pillar.FUNDAMENTALS,
        "EBIT / Interest Expense",
        {"ebit": inc.ebit, "interest_expense": inc.interest_expense}, p,
        reason="EBIT or interest expense unavailable (interest may be nil)",
    )

    # DSCR. The current portion of long-term debt is not separately reported
    # in most Indian filings, so short-term borrowings stand in for it. That
    # overstates the denominator for companies using short-term debt as
    # working-capital finance, which makes this a conservative reading — and
    # the substitution is disclosed rather than hidden.
    numerator = None
    if inc.ebitda is not None:
        numerator = inc.ebitda - (inc.tax_expense or 0.0)
    denominator = None
    if inc.interest_expense is not None or bal.short_term_debt is not None:
        denominator = (inc.interest_expense or 0.0) + (bal.short_term_debt or 0.0)
    out["dscr"] = _m(
        "dscr", "Debt Service Coverage Ratio",
        safe_div(numerator, denominator), Unit.TIMES, Pillar.FUNDAMENTALS,
        "(EBITDA - Tax) / (Interest + Short-term Debt)",
        {
            "ebitda": inc.ebitda,
            "tax_expense": inc.tax_expense,
            "interest_expense": inc.interest_expense,
            "short_term_debt": bal.short_term_debt,
        }, p,
        reason="EBITDA or debt service components unavailable",
        notes=["Short-term borrowings used as proxy for current portion of long-term debt."],
    )
    out["equity_multiplier"] = _m(
        "equity_multiplier", "Equity Multiplier",
        safe_div(bal.total_assets, bal.shareholders_equity), Unit.TIMES, Pillar.CONTEXT,
        "Total Assets / Shareholders' Equity",
        {"total_assets": bal.total_assets, "shareholders_equity": bal.shareholders_equity}, p,
        reason="total assets or equity unavailable",
        notes=["Closing values. The DuPont screen uses the average-based multiplier."],
    )
    return out


# ---------------------------------------------------------------------------
# Liquidity. Closing values.
# ---------------------------------------------------------------------------
def liquidity_ratios(cur: AnnualPeriod) -> dict[str, MetricValue]:
    bal = cur.balance
    p = cur.label
    out: dict[str, MetricValue] = {}

    out["current_ratio"] = _m(
        "current_ratio", "Current Ratio",
        safe_div(bal.total_current_assets, bal.total_current_liabilities),
        Unit.RATIO, Pillar.FUNDAMENTALS,
        "Current Assets / Current Liabilities",
        {
            "current_assets": bal.total_current_assets,
            "current_liabilities": bal.total_current_liabilities,
        }, p,
        reason="current assets or current liabilities unavailable",
    )

    quick_assets = None
    if bal.total_current_assets is not None:
        quick_assets = bal.total_current_assets - (bal.inventory or 0.0)
    out["quick_ratio"] = _m(
        "quick_ratio", "Quick Ratio",
        safe_div(quick_assets, bal.total_current_liabilities), Unit.RATIO, Pillar.FUNDAMENTALS,
        "(Current Assets - Inventory) / Current Liabilities",
        {
            "current_assets": bal.total_current_assets,
            "inventory": bal.inventory,
            "current_liabilities": bal.total_current_liabilities,
        }, p,
        reason="current assets or current liabilities unavailable",
    )
    out["net_working_capital"] = _m(
        "net_working_capital", "Net Working Capital",
        bal.net_working_capital, Unit.CRORE, Pillar.CONTEXT,
        "Current Assets - Current Liabilities",
        {
            "current_assets": bal.total_current_assets,
            "current_liabilities": bal.total_current_liabilities,
        }, p,
        reason="current assets or current liabilities unavailable",
    )
    return out


# ---------------------------------------------------------------------------
# Efficiency and working capital. Average balance-sheet values, since each
# ratio compares a full year's flow against the assets that generated it.
# ---------------------------------------------------------------------------
def efficiency_ratios(
    cur: AnnualPeriod, prev: AnnualPeriod | None = None
) -> dict[str, MetricValue]:
    inc, bal = cur.income, cur.balance
    prev_bal = prev.balance if prev else None
    p = cur.label
    out: dict[str, MetricValue] = {}

    avg_assets, notes_a = _avg_bs(bal.total_assets, prev_bal.total_assets if prev_bal else None)
    out["asset_turnover"] = _m(
        "asset_turnover", "Asset Turnover",
        safe_div(inc.revenue, avg_assets), Unit.TIMES, Pillar.CONTEXT,
        "Revenue / Average Total Assets",
        {"revenue": inc.revenue, "avg_total_assets": avg_assets}, p,
        reason="total assets unavailable", notes=notes_a,
    )

    avg_nfa, notes_f = _avg_bs(bal.net_fixed_assets, prev_bal.net_fixed_assets if prev_bal else None)
    out["fixed_asset_turnover"] = _m(
        "fixed_asset_turnover", "Fixed Asset Turnover",
        safe_div(inc.revenue, avg_nfa), Unit.TIMES, Pillar.CONTEXT,
        "Revenue / Average Net Fixed Assets",
        {"revenue": inc.revenue, "avg_net_fixed_assets": avg_nfa}, p,
        reason="net fixed assets unavailable", notes=notes_f,
    )

    avg_inv, notes_i = _avg_bs(bal.inventory, prev_bal.inventory if prev_bal else None)
    inv_days = safe_div(avg_inv, inc.cogs)
    out["inventory_days"] = _m(
        "inventory_days", "Inventory Days",
        None if inv_days is None else inv_days * DAYS_IN_YEAR, Unit.DAYS, Pillar.CONTEXT,
        "(Average Inventory / COGS) x 365",
        {"avg_inventory": avg_inv, "cogs": inc.cogs}, p,
        reason="inventory or COGS unavailable", notes=notes_i,
    )

    avg_rec, notes_r = _avg_bs(bal.receivables, prev_bal.receivables if prev_bal else None)
    dso = safe_div(avg_rec, inc.revenue)
    out["receivable_days"] = _m(
        "receivable_days", "Receivable Days (DSO)",
        None if dso is None else dso * DAYS_IN_YEAR, Unit.DAYS, Pillar.CONTEXT,
        "(Average Receivables / Revenue) x 365",
        {"avg_receivables": avg_rec, "revenue": inc.revenue}, p,
        reason="receivables unavailable", notes=notes_r,
    )

    avg_pay, notes_p = _avg_bs(bal.payables, prev_bal.payables if prev_bal else None)
    dpo = safe_div(avg_pay, inc.cogs)
    out["payable_days"] = _m(
        "payable_days", "Payable Days (DPO)",
        None if dpo is None else dpo * DAYS_IN_YEAR, Unit.DAYS, Pillar.CONTEXT,
        "(Average Payables / COGS) x 365",
        {"avg_payables": avg_pay, "cogs": inc.cogs}, p,
        reason="payables or COGS unavailable", notes=notes_p,
    )

    ccc = None
    parts = [out["inventory_days"].value, out["receivable_days"].value, out["payable_days"].value]
    if all(v is not None for v in parts):
        ccc = parts[0] + parts[1] - parts[2]
    out["cash_conversion_cycle"] = _m(
        "cash_conversion_cycle", "Cash Conversion Cycle",
        ccc, Unit.DAYS, Pillar.CONTEXT,
        "Inventory Days + Receivable Days - Payable Days",
        {
            "inventory_days": parts[0],
            "receivable_days": parts[1],
            "payable_days": parts[2],
        }, p,
        reason="one or more working-capital day-counts unavailable",
        notes=(
            ["Negative cycle: the company is financed by its suppliers."]
            if (ccc or 0) < 0 else []
        ),
    )
    out["nwc_to_revenue"] = _m(
        "nwc_to_revenue", "Net Working Capital / Revenue",
        pct(bal.net_working_capital, inc.revenue), Unit.PCT, Pillar.CONTEXT,
        "Net Working Capital / Revenue",
        {"net_working_capital": bal.net_working_capital, "revenue": inc.revenue}, p,
        reason="net working capital unavailable",
    )
    return out


# ---------------------------------------------------------------------------
# Per-share and market-based ratios. Require MarketData; every one of these
# is unavailable rather than guessed when no price is supplied.
# ---------------------------------------------------------------------------
def per_share_ratios(
    cur: AnnualPeriod,
    market: MarketData | None = None,
    prev: AnnualPeriod | None = None,
) -> dict[str, MetricValue]:
    inc, bal = cur.income, cur.balance
    p = cur.label
    out: dict[str, MetricValue] = {}

    shares = _shares(cur)
    eps = safe_div(inc.pat, shares)
    out["eps"] = _m(
        "eps", "EPS (diluted)", eps, Unit.RUPEES, Pillar.CONTEXT,
        "PAT / Diluted Shares",
        {"pat": inc.pat, "diluted_shares": shares}, p,
        reason="share count unavailable",
    )
    bvps = safe_div(bal.shareholders_equity, shares)
    out["bvps"] = _m(
        "bvps", "Book Value per Share", bvps, Unit.RUPEES, Pillar.CONTEXT,
        "Shareholders' Equity / Shares",
        {"shareholders_equity": bal.shareholders_equity, "shares": shares}, p,
        reason="equity or share count unavailable",
    )
    out["dividend_payout"] = _m(
        "dividend_payout", "Dividend Payout Ratio",
        pct(inc.dps, eps), Unit.PCT, Pillar.CONTEXT,
        "DPS / EPS",
        {"dps": inc.dps, "eps": eps}, p,
        reason="DPS or EPS unavailable",
    )

    if market is None:
        for name, label, unit in [
            ("pe_ratio", "P/E (trailing)", Unit.TIMES),
            ("pb_ratio", "P/B", Unit.TIMES),
            ("ev_ebitda", "EV / EBITDA", Unit.TIMES),
            ("ev_sales", "EV / Sales", Unit.TIMES),
            ("p_fcf", "Price / Free Cash Flow", Unit.TIMES),
            ("dividend_yield", "Dividend Yield", Unit.PCT),
            ("earnings_yield", "Earnings Yield", Unit.PCT),
            ("peg_ratio", "PEG Ratio", Unit.RATIO),
        ]:
            pillar = Pillar.VALUATION if name != "peg_ratio" else Pillar.CONTEXT
            out[name] = _m(name, label, None, unit, pillar, period=p,
                           reason="no market data supplied")
        return out

    cmp_ = market.cmp
    ev = market.enterprise_value(bal)

    out["pe_ratio"] = _m(
        "pe_ratio", "P/E (trailing)",
        safe_div(cmp_, eps) if (eps or 0) > 0 else None, Unit.TIMES, Pillar.VALUATION,
        "CMP / EPS",
        {"cmp": cmp_, "eps": eps}, p,
        reason="EPS non-positive; P/E not meaningful",
        notes=(
            ["Company loss-making or EPS nil; P/E suppressed rather than reported as negative."]
            if not (eps or 0) > 0 else []
        ),
    )
    out["pb_ratio"] = _m(
        "pb_ratio", "P/B",
        safe_div(cmp_, bvps) if (bvps or 0) > 0 else None, Unit.TIMES, Pillar.VALUATION,
        "CMP / BVPS",
        {"cmp": cmp_, "bvps": bvps}, p,
        reason="book value non-positive; P/B not meaningful",
    )
    out["ev_ebitda"] = _m(
        "ev_ebitda", "EV / EBITDA",
        safe_div(ev, inc.ebitda) if (inc.ebitda or 0) > 0 else None,
        Unit.TIMES, Pillar.VALUATION,
        "Enterprise Value / EBITDA",
        {"enterprise_value": ev, "ebitda": inc.ebitda, "market_cap": market.market_cap,
         "net_debt": bal.net_debt}, p,
        reason="EV or positive EBITDA unavailable",
    )
    out["ev_sales"] = _m(
        "ev_sales", "EV / Sales",
        safe_div(ev, inc.revenue), Unit.TIMES, Pillar.VALUATION,
        "Enterprise Value / Revenue",
        {"enterprise_value": ev, "revenue": inc.revenue}, p,
        reason="EV unavailable",
    )
    fcf = cur.cashflow.free_cash_flow
    out["p_fcf"] = _m(
        "p_fcf", "Price / Free Cash Flow",
        safe_div(market.market_cap, fcf) if (fcf or 0) > 0 else None,
        Unit.TIMES, Pillar.VALUATION,
        "Market Cap / Free Cash Flow",
        {"market_cap": market.market_cap, "free_cash_flow": fcf}, p,
        reason="free cash flow non-positive; multiple not meaningful",
    )
    out["dividend_yield"] = _m(
        "dividend_yield", "Dividend Yield",
        pct(inc.dps, cmp_), Unit.PCT, Pillar.VALUATION,
        "DPS / CMP",
        {"dps": inc.dps, "cmp": cmp_}, p,
        reason="DPS unavailable",
    )
    out["earnings_yield"] = _m(
        "earnings_yield", "Earnings Yield",
        pct(eps, cmp_) if (eps or 0) > 0 else None, Unit.PCT, Pillar.VALUATION,
        "EPS / CMP",
        {"eps": eps, "cmp": cmp_}, p,
        reason="EPS non-positive",
    )

    # PEG against the latest year-on-year EPS growth. A single year of growth
    # is a fragile denominator, which is why PEG sits in CONTEXT and carries
    # no scoring weight — it is shown because analysts expect to see it.
    peg = None
    peg_reason = "prior-year EPS unavailable"
    prev_eps = safe_div(prev.income.pat, _shares(prev)) if prev else None
    if eps is not None and (prev_eps or 0) > 0:
        growth_pct = (eps / prev_eps - 1.0) * 100.0
        pe = out["pe_ratio"].value
        if pe is not None and growth_pct > 0:
            peg = pe / growth_pct
        else:
            peg_reason = "EPS growth non-positive or P/E unavailable; PEG undefined"
    out["peg_ratio"] = _m(
        "peg_ratio", "PEG Ratio", peg, Unit.RATIO, Pillar.CONTEXT,
        "P/E / EPS growth %",
        {"pe_ratio": out["pe_ratio"].value, "eps": eps, "prev_eps": prev_eps}, p,
        reason=peg_reason,
        notes=["Based on latest year-on-year EPS growth, not a forward estimate."],
    )
    return out


def compute_ratios(
    cur: AnnualPeriod,
    prev: AnnualPeriod | None = None,
    market: MarketData | None = None,
) -> dict[str, MetricValue]:
    """Every ratio for one period. The single entry point for this module."""
    out: dict[str, MetricValue] = {}
    out.update(profitability_ratios(cur))
    out.update(return_ratios(cur, prev))
    out.update(leverage_ratios(cur))
    out.update(liquidity_ratios(cur))
    out.update(efficiency_ratios(cur, prev))
    out.update(per_share_ratios(cur, market, prev))
    return out
