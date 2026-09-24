"""Relative valuation — peers and own history (PRD 11.2).

Two mean-reversion views of fair value: what the company would be worth on its
peers' multiples, and on its own historical multiples. Both need inputs that
only arrive with the Phase 3 peer-comparison agent (a peer set) and price
history mapped to fiscal years (own multiple history). Absent those — every
pure Phase 2 run — this returns ``available=False`` with a reason, and the
valuation falls back to the DCF alone. The logic is implemented and tested now
so it is ready the moment those inputs exist.

Every arithmetic step is Python; the peer set itself is the only judgement, and
that is the agent's job, not this module's.
"""

from __future__ import annotations

from statistics import median
from typing import Any

from app.engine.helpers import percentile_rank, safe_div
from app.models.decision import MultipleComparison, RelativeValuationResult
from app.models.statements import FinancialStatements, MarketData

# Multiples we know how to imply a per-share value from, and how.
_MULTIPLES = ("pe", "pb", "ev_ebitda", "ev_sales", "p_fcf")


def _company_multiples(
    statements: FinancialStatements, market: MarketData
) -> dict[str, dict[str, float | None]]:
    """Current multiple and the per-share driver behind each, for implied values."""
    inc = statements.latest.income
    bal = statements.latest.balance
    cmp_ = market.cmp
    shares = inc.shares_diluted or inc.shares_basic
    ev = market.enterprise_value(bal)
    net_debt = bal.net_debt or 0.0

    eps = inc.eps_diluted
    bvps = safe_div(bal.shareholders_equity, shares)
    fcf = statements.latest.cashflow.free_cash_flow

    return {
        "pe": {"value": safe_div(cmp_, eps) if (eps or 0) > 0 else None,
               "driver": eps, "kind": "equity_per_share"},
        "pb": {"value": safe_div(cmp_, bvps) if (bvps or 0) > 0 else None,
               "driver": bvps, "kind": "equity_per_share"},
        "ev_ebitda": {"value": safe_div(ev, inc.ebitda) if (inc.ebitda or 0) > 0 else None,
                      "driver": inc.ebitda, "kind": "enterprise", "net_debt": net_debt, "shares": shares},
        "ev_sales": {"value": safe_div(ev, inc.revenue),
                     "driver": inc.revenue, "kind": "enterprise", "net_debt": net_debt, "shares": shares},
        "p_fcf": {"value": safe_div(market.market_cap, fcf) if (fcf or 0) > 0 else None,
                  "driver": fcf, "kind": "equity_total", "shares": shares},
    }


def _implied_per_share(multiple: str, benchmark: float, spec: dict[str, Any]) -> float | None:
    """Per-share value implied by applying ``benchmark`` multiple to the driver."""
    driver = spec.get("driver")
    if driver is None or benchmark is None:
        return None
    kind = spec["kind"]
    if kind == "equity_per_share":
        return benchmark * driver                     # multiple × EPS or BVPS = price
    shares = spec.get("shares")
    if not shares:
        return None
    if kind == "enterprise":
        implied_ev = benchmark * driver
        implied_equity = implied_ev - spec.get("net_debt", 0.0)
        return implied_equity / shares
    if kind == "equity_total":
        implied_market_cap = benchmark * driver
        return implied_market_cap / shares
    return None


def _compare(
    multiple: str, company_value: float | None, benchmark_values: list[float],
    spec: dict[str, Any], include_percentile: bool,
) -> MultipleComparison:
    present = [v for v in benchmark_values if v is not None]
    bench = median(present) if present else None
    premium = None
    if company_value is not None and bench:
        premium = (company_value / bench - 1.0) * 100.0
    implied = _implied_per_share(multiple, bench, spec) if bench is not None else None
    pct = None
    if include_percentile and company_value is not None and present:
        pct = percentile_rank(company_value, present + [company_value])
    return MultipleComparison(
        multiple=multiple,
        company_value=None if company_value is None else round(company_value, 4),
        benchmark_value=None if bench is None else round(bench, 4),
        premium_discount_pct=None if premium is None else round(premium, 2),
        implied_value_per_share=None if implied is None else round(implied, 2),
        peer_percentile=None if pct is None else round(pct, 1),
    )


def compute_relative_valuation(
    statements: FinancialStatements,
    market: MarketData | None,
    *,
    peer_multiples: dict[str, list[float]] | None = None,
    own_history_multiples: dict[str, list[float]] | None = None,
    quality_percentile: float | None = None,
    config: dict[str, Any] | None = None,
) -> RelativeValuationResult:
    """Peer-relative and own-history fair value.

    ``peer_multiples`` maps a multiple name to the peer set's values (excluding
    this company). ``own_history_multiples`` maps a multiple to this company's
    own historical series. Either may be absent.
    """
    if market is None:
        return RelativeValuationResult(available=False, notes=["no market data for relative valuation"])

    specs = _company_multiples(statements, market)
    peer_multiples = peer_multiples or {}
    own_history_multiples = own_history_multiples or {}

    peer_count = max((len(v) for v in peer_multiples.values()), default=0)

    peer_comparisons: list[MultipleComparison] = []
    for m in _MULTIPLES:
        if m in peer_multiples and peer_multiples[m]:
            peer_comparisons.append(
                _compare(m, specs[m]["value"], peer_multiples[m], specs[m], include_percentile=True)
            )

    own_comparisons: list[MultipleComparison] = []
    for m in _MULTIPLES:
        if m in own_history_multiples and own_history_multiples[m]:
            own_comparisons.append(
                _compare(m, specs[m]["value"], own_history_multiples[m], specs[m], include_percentile=False)
            )

    implied = [c.implied_value_per_share for c in peer_comparisons + own_comparisons
               if c.implied_value_per_share is not None]
    implied_low = min(implied) if implied else None
    implied_high = max(implied) if implied else None

    # Premium-vs-quality decomposition (PRD 11.2): is a valuation premium
    # justified by superior fundamentals?
    valuation_percentile = None
    premium_justified = None
    vals = [c.peer_percentile for c in peer_comparisons if c.peer_percentile is not None]
    if vals:
        valuation_percentile = round(sum(vals) / len(vals), 1)
        if quality_percentile is not None:
            # High valuation percentile = expensive; justified only if quality ranks at least as high.
            premium_justified = quality_percentile >= valuation_percentile

    notes: list[str] = []
    available = bool(peer_comparisons or own_comparisons)
    if not available:
        notes.append(
            "No peer set or own-multiple history supplied; relative valuation "
            "is unavailable (arrives with the Phase 3 peer agent). DCF stands alone."
        )
    if 0 < peer_count < 3:
        notes.append(f"only {peer_count} peer(s) supplied; below the 3 needed for a stable median (veto V10)")

    return RelativeValuationResult(
        available=available,
        valid_peer_count=peer_count,
        peer_comparisons=peer_comparisons,
        own_history_comparisons=own_comparisons,
        implied_low=implied_low,
        implied_high=implied_high,
        quality_percentile=None if quality_percentile is None else round(quality_percentile, 1),
        valuation_percentile=valuation_percentile,
        premium_justified=premium_justified,
        notes=notes,
    )
