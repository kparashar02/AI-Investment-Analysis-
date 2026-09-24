"""Assembly of the complete metric set.

The single public entry point of the deterministic layer. Given normalised
statements and optional market data, it returns a :class:`MetricSet`
containing every computed metric, screen and series, scored against the
configured curves.

This function is the entirety of what Phase 1 delivers, and it deliberately
runs without an API key, without a network connection and without a language
model. If it cannot be trusted, nothing built on top of it can be.
"""

from __future__ import annotations

from app.config.settings import load_thresholds, load_weights
from app.engine import cashflow_quality, growth, ratios, screens
from app.engine.helpers import pct
from app.engine.normalisation import apply_scores, is_bfsi, pillar_score, sector_key
from app.models.metrics import MetricSeries, MetricSet, MetricValue, Pillar, Unit
from app.models.statements import FinancialStatements, MarketData


def _screen_metrics(metric_screens: dict) -> dict[str, MetricValue]:
    """Expose screen scores as scoreable metrics.

    The Piotroski and Altman scores are referenced by name in weights.yaml,
    so they must appear in the metric map like any other component. Their
    caveats travel with them.
    """
    out: dict[str, MetricValue] = {}

    piotroski = metric_screens.get("piotroski_f_score")
    if piotroski is not None:
        passed = [k for k, v in piotroski.components.items() if v is True]
        out["piotroski_f_score"] = MetricValue(
            name="piotroski_f_score", label="Piotroski F-Score",
            value=piotroski.score, unit=Unit.SCORE, pillar=Pillar.FUNDAMENTALS,
            formula="Count of 9 accounting signals passed",
            inputs_used={"signals_passed": float(len(passed))},
            unavailable_reason=(
                None if piotroski.score is not None else piotroski.interpretation
            ),
            notes=list(piotroski.caveats),
        )

    altman = metric_screens.get("altman_z_score")
    if altman is not None:
        out["altman_z_score"] = MetricValue(
            name="altman_z_score", label="Altman Z-Score",
            value=altman.score, unit=Unit.SCORE, pillar=Pillar.RISK,
            formula=altman.variant or "Altman Z",
            inputs_used={
                k: float(v) for k, v in altman.components.items()
                if isinstance(v, int | float)
            },
            unavailable_reason=(
                None if altman.score is not None else altman.interpretation
            ),
            notes=list(altman.caveats),
        )
    return out


def _build_series(statements: FinancialStatements) -> dict[str, MetricSeries]:
    """Oldest-first series for the report's trend charts."""
    periods = list(reversed(statements.annual))
    labels = [p.label for p in periods]

    def series(name: str, label: str, unit: Unit, values: list[float | None]) -> MetricSeries:
        return MetricSeries(name=name, label=label, unit=unit, periods=labels, values=values)

    return {
        "revenue": series("revenue", "Revenue", Unit.CRORE,
                          [p.income.revenue for p in periods]),
        "ebitda": series("ebitda", "EBITDA", Unit.CRORE,
                         [p.income.ebitda for p in periods]),
        "pat": series("pat", "PAT", Unit.CRORE, [p.income.pat for p in periods]),
        "eps": series("eps", "EPS (diluted)", Unit.RUPEES,
                      [p.income.eps_diluted for p in periods]),
        "gross_margin": series("gross_margin", "Gross Margin", Unit.PCT,
                               [pct(p.income.gross_profit, p.income.revenue) for p in periods]),
        "ebitda_margin": series("ebitda_margin", "EBITDA Margin", Unit.PCT,
                                [pct(p.income.ebitda, p.income.revenue) for p in periods]),
        "net_margin": series("net_margin", "Net Margin", Unit.PCT,
                             [pct(p.income.pat, p.income.revenue) for p in periods]),
        "operating_cash_flow": series("operating_cash_flow", "Operating Cash Flow", Unit.CRORE,
                                      [p.cashflow.operating_cash_flow for p in periods]),
        "free_cash_flow": series("free_cash_flow", "Free Cash Flow", Unit.CRORE,
                                 [p.cashflow.free_cash_flow for p in periods]),
        "capex": series("capex", "Capex", Unit.CRORE, [p.cashflow.capex for p in periods]),
        "total_debt": series("total_debt", "Total Debt", Unit.CRORE,
                             [p.balance.total_debt for p in periods]),
        "shareholders_equity": series("shareholders_equity", "Shareholders' Equity", Unit.CRORE,
                                      [p.balance.shareholders_equity for p in periods]),
    }


def compute_metric_set(
    statements: FinancialStatements,
    market: MarketData | None = None,
    peer_values: dict[str, list[float | None]] | None = None,
) -> MetricSet:
    """Compute every metric, screen and series for one company.

    ``peer_values`` is optional and unused in Phase 1; when the Peer
    Comparison Agent supplies it, each metric additionally receives a peer
    percentile and the blended score defined in ``thresholds.yaml``.
    """
    if not statements.annual:
        raise ValueError("no annual periods supplied")

    thresholds = load_thresholds()
    cur = statements.latest
    prev = statements.previous
    prev2 = statements.annual[2] if len(statements.annual) > 2 else None

    key = sector_key(statements.sector, statements.industry)

    metrics: dict[str, MetricValue] = {}
    metrics.update(ratios.compute_ratios(cur, prev, market))
    metrics.update(growth.compute_growth(statements))
    metrics.update(cashflow_quality.compute_cashflow_quality(cur))

    metric_screens = {
        "piotroski_f_score": screens.piotroski_f_score(cur, prev, prev2),
        "altman_z_score": screens.altman_z_score(cur, market),
        "dupont": screens.dupont_decomposition(cur, prev),
        "cash_conversion_streak": cashflow_quality.conversion_streak(statements),
    }
    metrics.update(_screen_metrics(metric_screens))

    apply_scores(metrics, sector=key, peer_values=peer_values, thresholds=thresholds)

    warnings: list[str] = []
    if statements.data_quality is not None:
        warnings.extend(statements.data_quality.warnings)

    passed, deviation = cur.balance.balance_check()
    if not passed:
        warnings.append(
            "Balance sheet does not balance for "
            f"{cur.label}"
            + (f" (deviation {deviation:.2f}% of total assets)" if deviation is not None else "")
            + ". Every ratio built on this period is suspect — a mapping error in "
            "the normalisation layer is the usual cause."
        )
    if len(statements.annual) < 5:
        warnings.append(
            f"Only {len(statements.annual)} annual period(s) available; growth "
            f"metrics cover a shorter window than the intended five years."
        )
    if is_bfsi(statements.sector, statements.industry):
        warnings.append(
            "Sector appears to be BFSI. Veto rule V8 excludes banks, NBFCs and "
            "insurers from V1: an FCFF-based DCF is invalid for them and the "
            "metrics that matter (NIM, GNPA, CASA, CAR) are not implemented. "
            "The ratios below are computed but should not be used to rate."
        )
    if market is None:
        warnings.append("No market data supplied; all valuation metrics unavailable.")

    return MetricSet(
        ticker=statements.ticker,
        company_name=statements.company_name,
        sector=statements.sector,
        sector_key=key,
        period=cur.label,
        thresholds_version=str(thresholds.get("version", "")),
        metrics=metrics,
        series=_build_series(statements),
        screens=metric_screens,
        warnings=warnings,
    )


def pillar_preview(metric_set: MetricSet) -> dict[str, dict]:
    """Per-pillar scores for the pillars Phase 1 can actually compute.

    This is *not* the composite score. Fundamentals, growth and cash flow are
    computable from statements alone; valuation needs a fair value from the
    Phase 2 DCF, and industry, news and risk need the Phase 3 agents. The
    composite is deliberately not produced here, because a composite built on
    four of seven pillars silently reweighted would be a different model
    wearing the same name.
    """
    weights = load_weights()
    out: dict[str, dict] = {}
    for pillar in ("fundamentals", "growth", "cashflow"):
        score, contributions, notes = pillar_score(metric_set.metrics, pillar, weights)
        out[pillar] = {
            "score": score,
            "weight": (weights.get("pillars") or {}).get(pillar),
            "contributions": contributions,
            "notes": notes,
        }
    return out
