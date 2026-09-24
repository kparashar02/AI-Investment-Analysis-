"""Growth metrics: rates, consistency and margin direction.

**Naming convention, stated up front because it is where automated ratio
sheets most often mislead.** A "5-year CAGR" is reported by different data
providers to mean five compounding intervals (six data points) or a span of
five fiscal years (four intervals). Rather than pick one silently, the scored
growth metrics here are named without a year suffix — ``revenue_cagr`` — and
computed over the *entire available annual window*. Each one records its
``span_years`` and ``intervals`` in ``inputs_used``, and its label renders the
actual endpoints, e.g. "Revenue CAGR (FY2022 to FY2026, 4y)". A reader can
therefore never be misled about what window a growth rate covers.

Fixed-window rates over three compounding intervals are also computed, as
context only, where six or more years of history exist.
"""

from __future__ import annotations

from app.engine.helpers import cagr, consistency_score, ols_slope, pct, yoy_growth
from app.models.metrics import MetricValue, Pillar, Unit
from app.models.statements import AnnualPeriod, FinancialStatements

MIN_PERIODS_FOR_GROWTH = 3


def _series(statements: FinancialStatements, extractor) -> list[float | None]:
    """Oldest-first series across every annual period."""
    return statements.series(extractor)


def _cagr_metric(
    name: str,
    label: str,
    series: list[float | None],
    labels: list[str],
    pillar: Pillar,
) -> MetricValue:
    """CAGR over the full available window, with endpoints disclosed."""
    present = [(lbl, v) for lbl, v in zip(labels, series, strict=True) if v is not None]
    if len(present) < MIN_PERIODS_FOR_GROWTH:
        return MetricValue(
            name=name, label=label, value=None, unit=Unit.PCT, pillar=pillar,
            unavailable_reason=(
                f"requires at least {MIN_PERIODS_FOR_GROWTH} annual periods; "
                f"{len(present)} available"
            ),
        )

    (first_label, begin), (last_label, end) = present[0], present[-1]
    intervals = len(present) - 1
    value, reason = cagr(begin, end, intervals)
    span = len(present)

    return MetricValue(
        name=name,
        label=f"{label} ({first_label} to {last_label}, {intervals}y)",
        value=value,
        unit=Unit.PCT,
        pillar=pillar,
        period=f"{first_label}-{last_label}",
        formula=f"(end / begin) ^ (1 / {intervals}) - 1",
        inputs_used={
            "begin": begin,
            "end": end,
            "intervals": float(intervals),
            "span_years": float(span),
        },
        unavailable_reason=reason,
        notes=(
            []
            if len(present) == len(series)
            else [f"{len(series) - len(present)} period(s) skipped for missing data."]
        ),
    )


def compute_growth(statements: FinancialStatements) -> dict[str, MetricValue]:
    """Every growth metric. The single entry point for this module."""
    labels = [p.label for p in reversed(statements.annual)]
    out: dict[str, MetricValue] = {}

    revenue = _series(statements, lambda p: p.income.revenue)
    ebitda = _series(statements, lambda p: p.income.ebitda)
    pat = _series(statements, lambda p: p.income.pat)
    eps = _series(statements, lambda p: p.income.eps_diluted)
    fcf = _series(statements, lambda p: p.cashflow.free_cash_flow)

    out["revenue_cagr"] = _cagr_metric("revenue_cagr", "Revenue CAGR", revenue, labels, Pillar.GROWTH)
    out["ebitda_cagr"] = _cagr_metric("ebitda_cagr", "EBITDA CAGR", ebitda, labels, Pillar.GROWTH)
    out["pat_cagr"] = _cagr_metric("pat_cagr", "PAT CAGR", pat, labels, Pillar.GROWTH)
    out["eps_cagr"] = _cagr_metric("eps_cagr", "EPS CAGR", eps, labels, Pillar.GROWTH)
    out["fcf_cagr"] = _cagr_metric("fcf_cagr", "FCF CAGR", fcf, labels, Pillar.CASHFLOW)

    # Fixed three-interval window, context only, where the history allows it.
    if len([v for v in revenue if v is not None]) >= 4:
        window = revenue[-4:]
        window_labels = labels[-4:]
        value, reason = cagr(window[0], window[-1], 3)
        out["revenue_cagr_3y"] = MetricValue(
            name="revenue_cagr_3y",
            label=f"Revenue CAGR ({window_labels[0]} to {window_labels[-1]}, 3y)",
            value=value, unit=Unit.PCT, pillar=Pillar.CONTEXT,
            formula="(end / begin) ^ (1/3) - 1",
            inputs_used={"begin": window[0] or 0.0, "end": window[-1] or 0.0, "intervals": 3.0},
            unavailable_reason=reason,
        )

    # Latest year-on-year growth.
    growths = yoy_growth(revenue)
    latest_yoy = growths[-1] if growths else None
    out["revenue_growth_yoy"] = MetricValue(
        name="revenue_growth_yoy",
        label=f"Revenue Growth YoY ({labels[-1]})" if labels else "Revenue Growth YoY",
        value=latest_yoy, unit=Unit.PCT, pillar=Pillar.CONTEXT,
        formula="(Revenue_t / Revenue_t-1) - 1",
        inputs_used=(
            {"current": revenue[-1], "prior": revenue[-2]}
            if len(revenue) >= 2 and revenue[-1] is not None and revenue[-2] is not None
            else {}
        ),
        unavailable_reason="fewer than two annual periods, or non-positive base",
    )

    pat_growths = yoy_growth(pat)
    out["pat_growth_yoy"] = MetricValue(
        name="pat_growth_yoy",
        label=f"PAT Growth YoY ({labels[-1]})" if labels else "PAT Growth YoY",
        value=pat_growths[-1] if pat_growths else None,
        unit=Unit.PCT, pillar=Pillar.CONTEXT,
        formula="(PAT_t / PAT_t-1) - 1",
        inputs_used=(
            {"current": pat[-1], "prior": pat[-2]}
            if len(pat) >= 2 and pat[-1] is not None and pat[-2] is not None
            else {}
        ),
        unavailable_reason="fewer than two annual periods, or non-positive base",
    )

    # Consistency of revenue growth. See helpers.consistency_score for why
    # volatile growth is scored below steady growth of the same mean.
    score, reason = consistency_score(growths)
    observed = [g for g in growths if g is not None]
    out["growth_consistency"] = MetricValue(
        name="growth_consistency",
        label="Growth Consistency",
        value=score, unit=Unit.SCORE, pillar=Pillar.GROWTH,
        formula="100 x clamp(1 - sigma(YoY growth) / |mean(YoY growth)|, 0, 1)",
        inputs_used={
            "observations": float(len(observed)),
            **{f"yoy_{i + 1}": g for i, g in enumerate(observed)},
        },
        unavailable_reason=reason,
        notes=["Population standard deviation; the reported years are the full history."],
    )

    # Margin direction. The slope, not the level — a 22% margin trending up is
    # a materially different business from a 22% margin trending down, and the
    # level alone cannot tell them apart.
    margins = [
        pct(p.income.ebitda, p.income.revenue) for p in reversed(statements.annual)
    ]
    slope = ols_slope(margins)
    out["ebitda_margin_trend"] = MetricValue(
        name="ebitda_margin_trend",
        label="EBITDA Margin Trend",
        value=slope, unit=Unit.PP_PER_YEAR, pillar=Pillar.GROWTH,
        formula="OLS slope of EBITDA margin against period index",
        inputs_used={
            f"margin_{lbl}": m
            for lbl, m in zip(labels, margins, strict=True)
            if m is not None
        },
        unavailable_reason="fewer than two periods with a computable EBITDA margin",
        notes=["Percentage points per year."],
    )
    return out


def margin_series(statements: FinancialStatements) -> dict[str, list[float | None]]:
    """Oldest-first margin series, for the report's trend charts."""
    periods: list[AnnualPeriod] = list(reversed(statements.annual))
    return {
        "gross_margin": [pct(p.income.gross_profit, p.income.revenue) for p in periods],
        "ebitda_margin": [pct(p.income.ebitda, p.income.revenue) for p in periods],
        "ebit_margin": [pct(p.income.ebit, p.income.revenue) for p in periods],
        "net_margin": [pct(p.income.pat, p.income.revenue) for p in periods],
    }
