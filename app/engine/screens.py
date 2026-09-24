"""Composite screens: Piotroski F-Score, Altman Z-Score, DuPont decomposition.

Each screen is implemented to its published definition, with any deviation
forced by data availability recorded in the result's ``caveats`` rather than
left implicit. Reproducing a named academic screen inexactly and still
calling it by that name is a real methodological failure, so the deviations
are stated on the output itself and repeated in docs/METHODOLOGY.md.
"""

from __future__ import annotations

from app.engine.helpers import average, safe_div
from app.models.metrics import ScreenResult
from app.models.statements import AnnualPeriod, BalanceSheet, MarketData


def piotroski_f_score(
    cur: AnnualPeriod,
    prev: AnnualPeriod | None,
    prev2: AnnualPeriod | None = None,
) -> ScreenResult:
    """Piotroski (2000) nine-signal accounting-strength score.

    Signals, one point each:

    ==  ============================================================
    1   ROA positive
    2   Operating cash flow positive
    3   ROA improved year on year
    4   Operating cash flow exceeds net income (accrual quality)
    5   Long-term leverage fell
    6   Current ratio improved
    7   No net new equity issued
    8   Gross margin improved
    9   Asset turnover improved
    ==  ============================================================

    Following the paper, ROA and asset turnover use **beginning-of-year**
    total assets, which is why ``prev2`` is needed to evaluate the prior
    year's ROA. Signal 5 uses long-term debt over closing total assets rather
    than the paper's average-assets denominator; the direction of change is
    unaffected except in edge cases, and the substitution is recorded below.

    Requires two years of data. Returns a score of ``None`` with the reason
    stated if that is unavailable — a partially-evaluated F-Score is
    misleading, since a missing signal is indistinguishable from a failed one.
    """
    if prev is None:
        return ScreenResult(
            name="piotroski_f_score", label="Piotroski F-Score", score=None, max_score=9.0,
            interpretation="Requires two consecutive annual periods.",
            caveats=["Only one annual period available."],
        )

    signals: dict[str, float | bool | None] = {}

    ci, cb, cc = cur.income, cur.balance, cur.cashflow
    pi, pb, pc = prev.income, prev.balance, prev.cashflow

    # Beginning-of-year assets, per the paper.
    boy_assets_cur = pb.total_assets
    boy_assets_prev = prev2.balance.total_assets if prev2 else None

    roa_cur = safe_div(ci.pat, boy_assets_cur)
    roa_prev = safe_div(pi.pat, boy_assets_prev)

    # 1. Profitability
    signals["roa_positive"] = (roa_cur is not None and roa_cur > 0)
    # 2. Cash from operations
    signals["ocf_positive"] = (cc.operating_cash_flow is not None and cc.operating_cash_flow > 0)
    # 3. Change in ROA
    signals["roa_improved"] = (
        roa_cur is not None and roa_prev is not None and roa_cur > roa_prev
    )
    # 4. Accrual quality
    signals["ocf_exceeds_pat"] = (
        cc.operating_cash_flow is not None and cc.operating_cash_flow > ci.pat
    )
    # 5. Change in leverage
    lev_cur = safe_div(cb.long_term_debt, cb.total_assets)
    lev_prev = safe_div(pb.long_term_debt, pb.total_assets)
    signals["leverage_fell"] = (
        lev_cur is not None and lev_prev is not None and lev_cur < lev_prev
    )
    # 6. Change in liquidity
    cr_cur = safe_div(cb.total_current_assets, cb.total_current_liabilities)
    cr_prev = safe_div(pb.total_current_assets, pb.total_current_liabilities)
    signals["current_ratio_improved"] = (
        cr_cur is not None and cr_prev is not None and cr_cur > cr_prev
    )
    # 7. Equity issuance
    shares_cur = ci.shares_diluted or ci.shares_basic
    shares_prev = pi.shares_diluted or pi.shares_basic
    signals["no_new_shares"] = (
        shares_cur is not None and shares_prev is not None and shares_cur <= shares_prev
    )
    # 8. Change in gross margin
    gm_cur = safe_div(ci.gross_profit, ci.revenue)
    gm_prev = safe_div(pi.gross_profit, pi.revenue)
    signals["gross_margin_improved"] = (
        gm_cur is not None and gm_prev is not None and gm_cur > gm_prev
    )
    # 9. Change in asset turnover
    at_cur = safe_div(ci.revenue, boy_assets_cur)
    at_prev = safe_div(pi.revenue, boy_assets_prev)
    signals["asset_turnover_improved"] = (
        at_cur is not None and at_prev is not None and at_cur > at_prev
    )

    score = float(sum(1 for v in signals.values() if v is True))

    if score >= 8:
        band = "Strong — accounting fundamentals improving on almost every signal."
    elif score >= 6:
        band = "Sound — most signals positive."
    elif score >= 4:
        band = "Mixed — as many signals deteriorating as improving."
    else:
        band = "Weak — accounting fundamentals deteriorating broadly."

    caveats = [
        "ROA and asset turnover use beginning-of-year total assets, per Piotroski (2000).",
        "Signal 5 uses long-term debt over closing total assets rather than average assets.",
    ]
    if prev2 is None:
        caveats.append(
            "Third year unavailable: prior-year ROA and asset turnover could not be "
            "computed on a beginning-of-year basis, so signals 3 and 9 score 0 by "
            "default. The score is a floor, not a measurement."
        )

    return ScreenResult(
        name="piotroski_f_score", label="Piotroski F-Score", score=score, max_score=9.0,
        interpretation=f"{score:.0f}/9. {band}",
        components=signals, variant="Piotroski (2000), 9 signals", caveats=caveats,
    )


def altman_z_score(
    cur: AnnualPeriod, market: MarketData | None = None
) -> ScreenResult:
    """Altman Z-Score for bankruptcy risk.

    Two variants, selected by data availability:

    * **Original (1968)**, for listed non-financial companies, used when a
      market capitalisation is available::

          Z = 1.2 X1 + 1.4 X2 + 3.3 X3 + 0.6 X4 + 1.0 X5

      Zones: distress below 1.81, grey 1.81-2.99, safe above 2.99.

    * **Z' (private-firm)**, used when no market price is available, which
      substitutes book equity for market value of equity::

          Z' = 0.717 X1 + 0.847 X2 + 3.107 X3 + 0.420 X4' + 0.998 X5

      Zones: distress below 1.23, grey 1.23-2.90, safe above 2.90.

    The variant actually used is recorded on the result, because comparing a
    Z against a Z' threshold is a silent error that makes a distressed
    company look safe.

    Not valid for banks, NBFCs or insurers — their balance-sheet structure
    breaks X1 and X4 entirely. Veto V8 excludes BFSI from the system before
    this is ever reached.
    """
    bal, inc = cur.balance, cur.income
    caveats: list[str] = []

    working_capital = bal.net_working_capital
    total_assets = bal.total_assets

    # Retained earnings are frequently not broken out separately in Indian
    # filings; reserves & surplus is the closest available proxy.
    retained = bal.retained_earnings
    if retained is None and bal.reserves is not None:
        retained = bal.reserves
        caveats.append("Reserves & surplus used as a proxy for retained earnings (X2).")

    x1 = safe_div(working_capital, total_assets)
    x2 = safe_div(retained, total_assets)
    x3 = safe_div(inc.ebit, total_assets)
    x5 = safe_div(inc.revenue, total_assets)

    use_original = market is not None and market.market_cap is not None
    if use_original:
        x4 = safe_div(market.market_cap, bal.total_liabilities)
        coefficients = (1.2, 1.4, 3.3, 0.6, 1.0)
        variant = "Altman Z (1968), listed non-financial"
        distress, safe_line = 1.81, 2.99
    else:
        x4 = safe_div(bal.shareholders_equity, bal.total_liabilities)
        coefficients = (0.717, 0.847, 3.107, 0.420, 0.998)
        variant = "Altman Z' (private-firm variant)"
        distress, safe_line = 1.23, 2.90
        caveats.append(
            "No market capitalisation available; book equity substituted for market "
            "value of equity and the private-firm coefficients and thresholds used."
        )

    components = {"X1": x1, "X2": x2, "X3": x3, "X4": x4, "X5": x5}
    if any(v is None for v in components.values()):
        missing = [k for k, v in components.items() if v is None]
        return ScreenResult(
            name="altman_z_score", label="Altman Z-Score", score=None,
            interpretation=f"Not computable: missing {', '.join(missing)}.",
            components=components, variant=variant, caveats=caveats,
        )

    z = sum(c * v for c, v in zip(coefficients, components.values(), strict=True))

    if z < distress:
        zone = f"DISTRESS zone (below {distress}). Veto rule V4 applies."
    elif z < safe_line:
        zone = f"GREY zone ({distress}-{safe_line})."
    else:
        zone = f"SAFE zone (above {safe_line})."

    return ScreenResult(
        name="altman_z_score", label="Altman Z-Score", score=z,
        interpretation=f"{z:.2f} — {zone}",
        components=components, variant=variant, caveats=caveats,
    )


def dupont_decomposition(
    cur: AnnualPeriod, prev: AnnualPeriod | None = None
) -> ScreenResult:
    """Three-step DuPont decomposition of ROE.

        ROE = Net Margin x Asset Turnover x Equity Multiplier

    All three factors are computed on an **average** balance-sheet basis so
    that the identity holds exactly against the ROE reported by
    :func:`app.engine.ratios.return_ratios`. That exactness is worth the
    trouble: the whole point of DuPont is to say *why* ROE is what it is, and
    a decomposition whose product does not equal the thing being decomposed
    cannot support that claim. A unit test asserts the identity to 1e-9.

    The analytical payoff is distinguishing an ROE earned on margin and
    turnover from one manufactured with leverage. Two companies at 25% ROE,
    one at 1.2x and one at 4.0x equity multiplier, are not comparable
    businesses.
    """
    inc, bal = cur.income, cur.balance
    prev_bal: BalanceSheet | None = prev.balance if prev else None
    caveats: list[str] = []

    avg_assets = average(
        bal.total_assets, prev_bal.total_assets if prev_bal else None
    )
    avg_equity = average(
        bal.shareholders_equity, prev_bal.shareholders_equity if prev_bal else None
    )
    if prev_bal is None:
        caveats.append("Prior-year balance sheet unavailable; closing values used.")

    net_margin = safe_div(inc.pat, inc.revenue)
    asset_turnover = safe_div(inc.revenue, avg_assets)
    equity_multiplier = safe_div(avg_assets, avg_equity)

    components: dict[str, float | bool | None] = {
        "net_margin": None if net_margin is None else net_margin * 100.0,
        "asset_turnover": asset_turnover,
        "equity_multiplier": equity_multiplier,
        "avg_total_assets": avg_assets,
        "avg_equity": avg_equity,
    }

    if net_margin is None or asset_turnover is None or equity_multiplier is None:
        return ScreenResult(
            name="dupont", label="DuPont Decomposition", score=None,
            interpretation="Not computable: missing margin, turnover or leverage input.",
            components=components, variant="Three-step, average balance-sheet basis",
            caveats=caveats,
        )

    roe = net_margin * asset_turnover * equity_multiplier
    components["roe_reconstructed"] = roe * 100.0

    # Attribute the ROE to its dominant driver, which is the sentence the
    # narrative agent will actually use.
    if equity_multiplier > 2.5:
        driver = (
            f"Leverage-driven: an equity multiplier of {equity_multiplier:.2f}x is "
            f"doing much of the work, so the ROE carries balance-sheet risk."
        )
    elif net_margin > 0.15 and asset_turnover < 1.0:
        driver = (
            f"Margin-driven: a {net_margin * 100:.1f}% net margin on "
            f"{asset_turnover:.2f}x asset turnover — pricing power rather than volume."
        )
    elif asset_turnover > 1.5:
        driver = (
            f"Turnover-driven: {asset_turnover:.2f}x asset turnover on a "
            f"{net_margin * 100:.1f}% margin — asset efficiency rather than pricing."
        )
    else:
        driver = (
            f"Balanced: {net_margin * 100:.1f}% margin, {asset_turnover:.2f}x turnover, "
            f"{equity_multiplier:.2f}x leverage, with no single dominant driver."
        )

    return ScreenResult(
        name="dupont", label="DuPont Decomposition", score=roe * 100.0,
        interpretation=(
            f"ROE {roe * 100:.2f}% = {net_margin * 100:.2f}% x "
            f"{asset_turnover:.4f}x x {equity_multiplier:.4f}x. {driver}"
        ),
        components=components, variant="Three-step, average balance-sheet basis",
        caveats=caveats,
    )
