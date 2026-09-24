"""Cash flow and earnings quality.

The metric that matters most here is ``ocf_to_pat``. Reported profit is an
accounting construct; operating cash flow is closer to observable fact. A
company that persistently reports profit it does not collect in cash is
either growing its working capital aggressively or recognising revenue it
should not, and in either case its earnings deserve a lower multiple than the
headline suggests. This is why cash flow gets its own pillar rather than
being folded into fundamentals, and why a sustained reading below 0.5
triggers veto rule V3 (PRD 12.6).
"""

from __future__ import annotations

from app.engine.helpers import pct, safe_div
from app.models.metrics import MetricValue, Pillar, ScreenResult, Unit
from app.models.statements import AnnualPeriod, FinancialStatements

POOR_CONVERSION_THRESHOLD = 0.5
POOR_CONVERSION_YEARS_FOR_VETO = 3


def compute_cashflow_quality(cur: AnnualPeriod) -> dict[str, MetricValue]:
    """Cash flow metrics for one period."""
    inc, cf = cur.income, cur.cashflow
    p = cur.label
    out: dict[str, MetricValue] = {}

    out["operating_cash_flow"] = MetricValue(
        name="operating_cash_flow", label="Operating Cash Flow",
        value=cf.operating_cash_flow, unit=Unit.CRORE, pillar=Pillar.CONTEXT, period=p,
        formula="Net cash from operating activities, as reported",
        inputs_used=(
            {"reported": cf.operating_cash_flow}
            if cf.operating_cash_flow is not None else {}
        ),
        unavailable_reason="cash flow statement unavailable",
    )
    out["free_cash_flow"] = MetricValue(
        name="free_cash_flow", label="Free Cash Flow",
        value=cf.free_cash_flow, unit=Unit.CRORE, pillar=Pillar.CONTEXT, period=p,
        formula="Operating Cash Flow - Capex",
        inputs_used={
            k: v for k, v in
            {"operating_cash_flow": cf.operating_cash_flow, "capex": cf.capex}.items()
            if v is not None
        },
        unavailable_reason="OCF or capex unavailable",
    )

    conversion = safe_div(cf.operating_cash_flow, inc.pat) if inc.pat > 0 else None
    out["ocf_to_pat"] = MetricValue(
        name="ocf_to_pat", label="OCF / PAT (Cash Conversion)",
        value=conversion, unit=Unit.RATIO, pillar=Pillar.CASHFLOW, period=p,
        formula="Operating Cash Flow / PAT",
        inputs_used={
            k: v for k, v in
            {"operating_cash_flow": cf.operating_cash_flow, "pat": inc.pat}.items()
            if v is not None
        },
        unavailable_reason=(
            "PAT non-positive; conversion ratio not meaningful"
            if inc.pat <= 0 else "operating cash flow unavailable"
        ),
        notes=(
            ["Below 1.0: reported profit is not fully converted to operating cash."]
            if conversion is not None and conversion < 1.0 else []
        ),
    )
    out["ocf_margin"] = MetricValue(
        name="ocf_margin", label="Operating Cash Flow Margin",
        value=pct(cf.operating_cash_flow, inc.revenue), unit=Unit.PCT,
        pillar=Pillar.CONTEXT, period=p,
        formula="Operating Cash Flow / Revenue",
        inputs_used={
            k: v for k, v in
            {"operating_cash_flow": cf.operating_cash_flow, "revenue": inc.revenue}.items()
            if v is not None
        },
        unavailable_reason="operating cash flow unavailable",
    )
    out["fcf_margin"] = MetricValue(
        name="fcf_margin", label="Free Cash Flow Margin",
        value=pct(cf.free_cash_flow, inc.revenue), unit=Unit.PCT,
        pillar=Pillar.CASHFLOW, period=p,
        formula="Free Cash Flow / Revenue",
        inputs_used={
            k: v for k, v in
            {"free_cash_flow": cf.free_cash_flow, "revenue": inc.revenue}.items()
            if v is not None
        },
        unavailable_reason="free cash flow unavailable",
    )
    out["capex_intensity"] = MetricValue(
        name="capex_intensity", label="Capex Intensity",
        value=pct(cf.capex, inc.revenue), unit=Unit.PCT,
        pillar=Pillar.CASHFLOW, period=p,
        formula="Capex / Revenue",
        inputs_used={
            k: v for k, v in {"capex": cf.capex, "revenue": inc.revenue}.items()
            if v is not None
        },
        unavailable_reason="capex unavailable",
        notes=[
            "Scored on a hump curve: near-zero capex can signal underinvestment "
            "as readily as capital discipline."
        ],
    )
    out["dividend_coverage"] = MetricValue(
        name="dividend_coverage", label="Dividend Coverage by FCF",
        value=safe_div(cf.free_cash_flow, cf.dividends_paid), unit=Unit.TIMES,
        pillar=Pillar.CONTEXT, period=p,
        formula="Free Cash Flow / Dividends Paid",
        inputs_used={
            k: v for k, v in
            {"free_cash_flow": cf.free_cash_flow, "dividends_paid": cf.dividends_paid}.items()
            if v is not None
        },
        unavailable_reason="FCF or dividends paid unavailable (dividend may be nil)",
    )
    return out


def conversion_streak(statements: FinancialStatements) -> ScreenResult:
    """Consecutive most-recent years with OCF/PAT below the poor-quality line.

    Exists to supply veto rule V3, which caps the rating at HOLD when a
    company has failed to convert profit into cash for three years running.
    A single weak year is ordinary — a working capital build, a one-off. Three
    in a row is a pattern, and the pattern is what the veto responds to.
    """
    ratios: list[tuple[str, float | None]] = []
    for period in statements.annual:            # newest-first
        pat = period.income.pat
        ocf = period.cashflow.operating_cash_flow
        ratios.append((period.label, safe_div(ocf, pat) if pat > 0 else None))

    streak = 0
    for _, ratio in ratios:
        if ratio is not None and ratio < POOR_CONVERSION_THRESHOLD:
            streak += 1
        else:
            break

    triggers = streak >= POOR_CONVERSION_YEARS_FOR_VETO
    if triggers:
        interpretation = (
            f"OCF/PAT below {POOR_CONVERSION_THRESHOLD} for {streak} consecutive "
            f"years. Earnings quality flag; veto rule V3 applies."
        )
    elif streak:
        interpretation = (
            f"OCF/PAT below {POOR_CONVERSION_THRESHOLD} in the last {streak} "
            f"year(s) — monitor, but not yet a pattern."
        )
    else:
        interpretation = "Latest-year cash conversion above the poor-quality threshold."

    return ScreenResult(
        name="cash_conversion_streak",
        label="Cash Conversion Streak",
        score=float(streak),
        max_score=float(len(ratios)),
        interpretation=interpretation,
        components={f"ocf_to_pat_{label}": ratio for label, ratio in ratios},
        caveats=(
            ["Years with non-positive PAT break the streak and are not counted as failures."]
            if any(r is None for _, r in ratios) else []
        ),
    )
