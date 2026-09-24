"""Discounted cash flow — two-stage FCFF (PRD 11.1).

Every number here is computed in pure Python from normalised statements and a
set of bounded assumptions. In Phase 2 the assumptions come from the
deterministic defaults in ``valuation.yaml`` (historical growth faded to a
terminal rate); in Phase 3 the Valuation Agent may propose an alternative
growth path, but it is validated against the *same* guardrails and every
arithmetic step still happens here. That division is the whole design (PRD 6):
the model may argue about the growth fade; it may not compute the fair value.

The DCF is always presented as a **range** via the sensitivity grid, never as a
single number — a point estimate dressed as precision is treated as a
methodological error (PRD 11.1).
"""

from __future__ import annotations

from typing import Any

from app.config.settings import load_valuation
from app.engine.helpers import cagr, safe_div
from app.models.decision import DCFResult, SensitivityGrid
from app.models.statements import FinancialStatements, MarketData


def _effective_tax_rate(statements: FinancialStatements, cfg: dict[str, Any]) -> float:
    """3-year average effective tax rate, capped at the statutory rate.

    Capping stops a one-off tax credit (a negative or tiny effective rate in
    one year) from inflating FCFF across the whole forecast."""
    forecast = cfg.get("forecast") or {}
    years = int(forecast.get("tax_averaging_years", 3))
    statutory = float(forecast.get("statutory_tax_rate_pct", 25.17)) / 100.0

    rates: list[float] = []
    for period in statements.annual[:years]:
        rate = period.income.effective_tax_rate
        if rate is not None and 0.0 <= rate <= 1.0:
            rates.append(rate)
    if not rates:
        return statutory
    avg = sum(rates) / len(rates)
    return min(avg, statutory)


def _wacc_build_up(
    statements: FinancialStatements,
    market: MarketData,
    cfg: dict[str, Any],
) -> dict[str, float] | None:
    """Cost of capital (PRD 11.1). Returns the build-up, or ``None`` if the
    equity value needed to weight it is unavailable."""
    wacc_cfg = cfg.get("wacc") or {}
    rf = float(wacc_cfg.get("risk_free_rate_pct", 7.0))
    erp = float(wacc_cfg.get("equity_risk_premium_pct", 7.0))
    beta_floor = float(wacc_cfg.get("beta_floor", 0.5))
    beta_cap = float(wacc_cfg.get("beta_cap", 2.0))
    beta_default = float(wacc_cfg.get("beta_default", 1.0))
    rd_floor = float(wacc_cfg.get("cost_of_debt_floor_pct", 3.0))

    raw_beta = market.beta if market.beta is not None else beta_default
    beta = max(beta_floor, min(beta_cap, raw_beta))
    beta_clamped = beta != raw_beta

    tax = _effective_tax_rate(statements, cfg)
    re = rf + beta * erp

    latest = statements.latest
    prev = statements.previous
    debt = latest.balance.total_debt or 0.0
    prev_debt = (prev.balance.total_debt if prev else None) or debt
    avg_debt = (debt + prev_debt) / 2.0 if (debt or prev_debt) else 0.0
    interest = latest.income.interest_expense
    rd_raw = safe_div(interest, avg_debt)
    rd = max(rd_floor, rd_raw * 100.0) if rd_raw is not None else rd_floor

    equity = market.market_cap
    if equity is None or equity <= 0:
        return None
    total = equity + debt
    e_w = equity / total
    d_w = debt / total

    wacc = e_w * re + d_w * rd * (1.0 - tax)
    return {
        "cost_of_equity_pct": re,
        "cost_of_debt_pct": rd,
        "beta_used": beta,
        "beta_clamped": 1.0 if beta_clamped else 0.0,
        "tax_rate": tax,
        "wacc_pct": wacc,
        "equity_weight": e_w,
        "debt_weight": d_w,
    }


def _ratio_average(statements: FinancialStatements, extractor, years: int) -> float | None:
    """Average of ``extractor(period)/revenue`` over the most recent ``years``."""
    ratios: list[float] = []
    for period in statements.annual[:years]:
        rev = period.income.revenue
        val = extractor(period)
        r = safe_div(val, rev)
        if r is not None:
            ratios.append(r)
    if not ratios:
        return None
    return sum(ratios) / len(ratios)


def default_assumptions(
    statements: FinancialStatements,
    cfg: dict[str, Any],
    terminal_growth_pct: float,
) -> dict[str, Any]:
    """The deterministic Phase 2 assumption set (PRD 8.9 division of labour).

    Revenue grows at its historical CAGR in year 1 and fades linearly to the
    terminal rate; margins, capex intensity and NWC intensity are held at their
    recent levels. Fully mechanical, so a fair value exists with no LLM.
    """
    da = cfg.get("default_assumptions") or {}
    horizon = int((cfg.get("forecast") or {}).get("horizon_years", 5))
    g_floor = float(da.get("revenue_growth_floor_pct", 0.0))
    g_cap = float(da.get("revenue_growth_cap_pct", 25.0))

    oldest_first = list(reversed(statements.annual))
    rev_series = [p.income.revenue for p in oldest_first]
    intervals = len(rev_series) - 1
    hist_cagr, _ = cagr(rev_series[0], rev_series[-1], intervals) if intervals >= 1 else (None, None)
    g1 = terminal_growth_pct if hist_cagr is None else max(g_floor, min(g_cap, hist_cagr))

    # Linear fade from g1 (year 1) to terminal growth (year n).
    if horizon == 1:
        growth_path = [terminal_growth_pct]
    else:
        step = (terminal_growth_pct - g1) / (horizon - 1)
        growth_path = [g1 + step * i for i in range(horizon)]

    latest = statements.latest
    ebit_margin = safe_div(latest.income.ebit, latest.income.revenue) or 0.0
    da_intensity = safe_div(latest.income.depreciation_amortisation, latest.income.revenue) or 0.0
    capex_intensity = _ratio_average(
        statements, lambda p: p.cashflow.capex, int(da.get("capex_averaging_years", 3))
    ) or 0.0
    nwc_intensity = _ratio_average(
        statements, lambda p: p.balance.net_working_capital, int(da.get("nwc_averaging_years", 3))
    ) or 0.0

    return {
        "horizon_years": horizon,
        "base_revenue": latest.income.revenue,
        "revenue_growth_path_pct": growth_path,
        "ebit_margin": ebit_margin,
        "da_intensity": da_intensity,
        "capex_intensity": capex_intensity,
        "nwc_intensity": nwc_intensity,
        "historical_revenue_cagr_pct": hist_cagr if hist_cagr is not None else float("nan"),
    }


def _forecast_fcff(assumptions: dict[str, Any], tax_rate: float) -> tuple[list[float], list[float]]:
    """Return ``(fcff_by_year, revenue_by_year)`` for the explicit horizon."""
    rev_prev = assumptions["base_revenue"]
    ebit_margin = assumptions["ebit_margin"]
    da_intensity = assumptions["da_intensity"]
    capex_intensity = assumptions["capex_intensity"]
    nwc_intensity = assumptions["nwc_intensity"]

    fcff: list[float] = []
    revenues: list[float] = []
    for growth in assumptions["revenue_growth_path_pct"]:
        rev_t = rev_prev * (1.0 + growth / 100.0)
        ebit_t = ebit_margin * rev_t
        da_t = da_intensity * rev_t
        capex_t = capex_intensity * rev_t
        dnwc_t = nwc_intensity * (rev_t - rev_prev)
        fcff_t = ebit_t * (1.0 - tax_rate) + da_t - capex_t - dnwc_t
        fcff.append(fcff_t)
        revenues.append(rev_t)
        rev_prev = rev_t
    return fcff, revenues


def _discount_to_ev(fcff: list[float], wacc_pct: float, terminal_growth_pct: float) -> float | None:
    """Present value of the explicit FCFF plus the terminal value."""
    w = wacc_pct / 100.0
    g = terminal_growth_pct / 100.0
    if w <= g:
        return None
    pv = 0.0
    for t, cf in enumerate(fcff, start=1):
        pv += cf / (1.0 + w) ** t
    n = len(fcff)
    terminal_value = fcff[-1] * (1.0 + g) / (w - g)
    pv += terminal_value / (1.0 + w) ** n
    return pv


def _fair_value_per_share(
    fcff: list[float], wacc_pct: float, terminal_growth_pct: float,
    net_debt: float, shares: float,
) -> float | None:
    ev = _discount_to_ev(fcff, wacc_pct, terminal_growth_pct)
    if ev is None or shares <= 0:
        return None
    equity_value = ev - net_debt
    return equity_value / shares


def _sensitivity_grid(
    fcff: list[float], base_wacc: float, base_growth: float,
    net_debt: float, shares: float, cfg: dict[str, Any],
) -> SensitivityGrid:
    sens = cfg.get("sensitivity") or {}
    wacc_deltas = [float(d) for d in sens.get("wacc_deltas_pct", [-2, -1, 0, 1, 2])]
    growth_points = [float(g) for g in sens.get("terminal_growth_points_pct", [3, 3.5, 4, 4.5, 5])]
    wacc_values = [base_wacc + d for d in wacc_deltas]

    grid: list[list[float | None]] = []
    for w in wacc_values:
        row: list[float | None] = []
        for g in growth_points:
            # Respect the same spread guardrail inside the grid.
            row.append(_fair_value_per_share(fcff, w, g, net_debt, shares))
        grid.append(row)
    return SensitivityGrid(
        wacc_values_pct=[round(w, 4) for w in wacc_values],
        growth_values_pct=growth_points,
        grid=grid,
        base_wacc_pct=round(base_wacc, 4),
        base_growth_pct=base_growth,
    )


def compute_dcf(
    statements: FinancialStatements,
    market: MarketData | None,
    config: dict[str, Any] | None = None,
    assumptions: dict[str, Any] | None = None,
) -> DCFResult:
    """Full two-stage FCFF DCF. Never raises for a data gap — returns an
    ``available=False`` result with a reason, so the caller can degrade."""
    cfg = config or load_valuation()

    if market is None or market.market_cap is None:
        return DCFResult(available=False, unavailable_reason="no market data; DCF needs an equity value")
    if len(statements.annual) < 2:
        return DCFResult(available=False, unavailable_reason="fewer than two annual periods")

    shares = statements.latest.income.shares_diluted or statements.latest.income.shares_basic
    if not shares:
        return DCFResult(available=False, unavailable_reason="no diluted share count for a per-share value")

    build = _wacc_build_up(statements, market, cfg)
    if build is None:
        return DCFResult(available=False, unavailable_reason="could not build WACC (missing equity value)")
    wacc = build["wacc_pct"]

    tg = cfg.get("terminal_growth") or {}
    g_min, g_max = float(tg.get("min_pct", 3.0)), float(tg.get("max_pct", 5.5))
    spread = float(tg.get("wacc_spread_min_pct", 1.5))

    flags: list[str] = []
    confidence = "BASE"

    # Terminal growth: the configured ceiling, but kept a safe margin below WACC.
    terminal_g = min(g_max, wacc - spread)
    if terminal_g < g_min:
        flags.append(
            f"terminal growth forced to {terminal_g:.2f}% (< floor {g_min}%) to stay "
            f"{spread}% below a low WACC of {wacc:.2f}%"
        )
        confidence = "LOW_CONFIDENCE"
    if wacc <= terminal_g:
        return DCFResult(
            available=False,
            unavailable_reason=f"WACC {wacc:.2f}% does not exceed terminal growth {terminal_g:.2f}%; "
            "terminal value is undefined",
        )
    if build["beta_clamped"]:
        flags.append("beta was clamped to its [floor, cap] band; the raw regression beta was out of range")
        confidence = "LOW_CONFIDENCE"
    if len(statements.annual) < 3:
        flags.append("fewer than three annual periods; growth and intensity averages rest on thin history")
        confidence = "LOW_CONFIDENCE"

    assumptions = assumptions or default_assumptions(statements, cfg, terminal_g)
    fcff, _revenues = _forecast_fcff(assumptions, build["tax_rate"])

    if fcff[-1] <= 0:
        flags.append("terminal-year FCFF is non-positive; the terminal value is not meaningful")
        confidence = "LOW_CONFIDENCE"

    net_debt = statements.latest.balance.net_debt or 0.0
    ev = _discount_to_ev(fcff, wacc, terminal_g)
    if ev is None:
        return DCFResult(available=False, unavailable_reason="discounting failed (WACC <= g)")
    equity_value = ev - net_debt
    fair_base = equity_value / shares

    # Range from the cells one step either side of the base case (PRD 11.1).
    sens_cfg = cfg.get("sensitivity") or {}
    w_step = float(sens_cfg.get("range_wacc_step_pct", 1.0))
    g_step = float(sens_cfg.get("range_growth_step_pct", 0.5))
    fair_low = _fair_value_per_share(fcff, wacc + w_step, max(g_min, terminal_g - g_step), net_debt, shares)
    fair_high = _fair_value_per_share(fcff, max(wacc - w_step, terminal_g + g_step + 0.01),
                                      terminal_g + g_step, net_debt, shares)
    # Guard ordering: low/high are the pessimistic/optimistic corners.
    lo = min(x for x in (fair_low, fair_base, fair_high) if x is not None)
    hi = max(x for x in (fair_low, fair_base, fair_high) if x is not None)

    # A range far wider than the base case means the DCF is too sensitive to its
    # assumptions to be a trustworthy anchor — flag it (PRD 11.1, honesty over
    # false precision).
    max_ratio = float(sens_cfg.get("max_range_ratio_for_confidence", 1.0))
    if fair_base > 0 and (hi - lo) / fair_base > max_ratio:
        flags.append(
            f"fair-value range ₹{lo:.0f}-₹{hi:.0f} spans {(hi - lo) / fair_base:.0%} of the "
            f"base case; the DCF is highly assumption-sensitive here"
        )
        confidence = "LOW_CONFIDENCE"

    terminal_value = fcff[-1] * (1.0 + terminal_g / 100.0) / (wacc / 100.0 - terminal_g / 100.0)

    return DCFResult(
        available=True,
        confidence=confidence,
        flags=flags,
        cost_of_equity_pct=round(build["cost_of_equity_pct"], 4),
        cost_of_debt_pct=round(build["cost_of_debt_pct"], 4),
        beta_used=round(build["beta_used"], 4),
        tax_rate=round(build["tax_rate"], 6),
        wacc_pct=round(wacc, 4),
        equity_weight=round(build["equity_weight"], 4),
        debt_weight=round(build["debt_weight"], 4),
        revenue_growth_path_pct=[round(g, 4) for g in assumptions["revenue_growth_path_pct"]],
        fcff_forecast=[round(f, 2) for f in fcff],
        terminal_growth_pct=round(terminal_g, 4),
        terminal_value=round(terminal_value, 2),
        enterprise_value=round(ev, 2),
        net_debt=round(net_debt, 2),
        equity_value=round(equity_value, 2),
        fair_value_base=round(fair_base, 2),
        fair_value_low=round(lo, 2),
        fair_value_high=round(hi, 2),
        sensitivity=_sensitivity_grid(fcff, wacc, terminal_g, net_debt, shares, cfg),
        assumptions={
            "ebit_margin": round(assumptions["ebit_margin"], 6),
            "da_intensity": round(assumptions["da_intensity"], 6),
            "capex_intensity": round(assumptions["capex_intensity"], 6),
            "nwc_intensity": round(assumptions["nwc_intensity"], 6),
        },
    )
