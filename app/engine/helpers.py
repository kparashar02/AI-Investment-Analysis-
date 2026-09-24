"""Arithmetic primitives for the deterministic layer.

Deliberately implemented in pure Python with no numpy or pandas dependency.
Two reasons:

1. **Reproducibility.** ``numpy`` reductions dispatch to whatever BLAS the
   machine has, and summation order can differ between builds. For a system
   whose central claim is that the same inputs produce a bit-identical score
   (PRD NF4), a dependency that can change the low-order bits of a mean is
   an unnecessary risk.
2. **Auditability.** A reader checking the methodology can follow every line
   here against a textbook formula.

The other convention worth stating: **``None`` propagates, it never becomes
zero.** A missing input yields a missing metric with a stated reason, because
silently substituting zero for "we don't know" is how a data gap turns into a
confidently wrong recommendation.
"""

from __future__ import annotations

import math

Number = float | int | None


def safe_div(numerator: Number, denominator: Number) -> float | None:
    """Divide, returning ``None`` rather than raising or coercing to zero."""
    if numerator is None or denominator is None:
        return None
    if denominator == 0:
        return None
    return numerator / denominator


def pct(numerator: Number, denominator: Number) -> float | None:
    """Ratio expressed as a percentage, per the natural-units convention."""
    result = safe_div(numerator, denominator)
    return None if result is None else result * 100.0


def average(*values: Number) -> float | None:
    """Mean of the supplied values, ignoring ``None``.

    Used for the two-point averages that flow/stock ratios require (ROE uses
    average equity, not closing equity). Returns ``None`` only if every value
    is missing, so a first-year company with no prior balance sheet falls back
    to its closing position rather than losing the metric entirely — the
    caller records that fallback in the metric's notes.
    """
    present = [v for v in values if v is not None]
    if not present:
        return None
    return sum(present) / len(present)


def mean(values: list[float]) -> float | None:
    if not values:
        return None
    return sum(values) / len(values)


def pstdev(values: list[float]) -> float | None:
    """Population standard deviation (divisor ``n``).

    Population rather than sample is a deliberate choice: the five reported
    years are the complete history under analysis, not a sample drawn from a
    larger population of that company's years. Stated here because the choice
    changes ``growth_consistency`` and therefore the growth pillar score.
    """
    if len(values) < 2:
        return None
    mu = sum(values) / len(values)
    variance = sum((v - mu) ** 2 for v in values) / len(values)
    return math.sqrt(variance)


def cagr(begin: Number, end: Number, years: int) -> tuple[float | None, str | None]:
    """Compound annual growth rate as a percentage.

    ``years`` is the number of *intervals*, so a 5-year CAGR needs six annual
    data points. Returns ``(value, reason_if_unavailable)``.

    CAGR is mathematically undefined when the base is non-positive, and it is
    meaningless when the sign flips (a swing from a loss to a profit has no
    compound growth rate). Both cases return ``None`` with a reason rather
    than a fabricated number — the single most common error in automated
    ratio sheets, and one that produces spectacular nonsense on turnaround
    companies.
    """
    if begin is None or end is None:
        return (None, "missing endpoint")
    if years <= 0:
        return (None, "non-positive interval count")
    if begin <= 0:
        return (None, f"non-positive base ({begin:,.1f}) — CAGR undefined")
    if end <= 0:
        return (None, f"non-positive endpoint ({end:,.1f}) — CAGR undefined")
    return (((end / begin) ** (1.0 / years) - 1.0) * 100.0, None)


def yoy_growth(series: list[float | None]) -> list[float | None]:
    """Year-on-year growth percentages for an oldest-first series.

    Returns ``len(series) - 1`` values. Growth off a non-positive base is
    ``None`` for the same reason CAGR is.
    """
    out: list[float | None] = []
    for prev, cur in zip(series, series[1:], strict=False):
        if prev is None or cur is None or prev <= 0:
            out.append(None)
        else:
            out.append((cur / prev - 1.0) * 100.0)
    return out


def ols_slope(values: list[float | None]) -> float | None:
    """Ordinary-least-squares slope of ``values`` against period index.

    Used for margin trend, in units of "metric units per year". Missing
    points are dropped and the surviving points keep their original index
    spacing, so a gap year correctly widens the run rather than compressing
    it.
    """
    points = [(i, v) for i, v in enumerate(values) if v is not None]
    if len(points) < 2:
        return None
    n = len(points)
    mean_x = sum(x for x, _ in points) / n
    mean_y = sum(y for _, y in points) / n
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in points)
    sxx = sum((x - mean_x) ** 2 for x, _ in points)
    if sxx == 0:
        return None
    return sxy / sxx


def consistency_score(growths: list[float | None]) -> tuple[float | None, str | None]:
    """Growth consistency on a 0-100 scale.

    ``100 * clamp(1 - sigma / |mu|, 0, 1)`` over the year-on-year growth
    series: a company growing 12%, 13%, 11%, 12% scores far higher than one
    growing 40%, -5%, 25%, 0% at the same average. Volatile growth is worth
    less than steady growth of the same mean, and this is where that belief
    enters the model.

    Undefined when mean growth is near zero, since the coefficient of
    variation explodes — reported as unavailable rather than as a spuriously
    extreme score.
    """
    present = [g for g in growths if g is not None]
    if len(present) < 2:
        return (None, "fewer than two growth observations")
    mu = mean(present)
    sigma = pstdev(present)
    if mu is None or sigma is None:
        return (None, "insufficient data")
    if abs(mu) < 1e-6:
        return (None, "mean growth ~0; coefficient of variation undefined")
    raw = 1.0 - (sigma / abs(mu))
    return (100.0 * clamp(raw, 0.0, 1.0), None)


def clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def interpolate(curve: list[tuple[float, float]], x: float) -> float:
    """Piecewise-linear interpolation over a monotonically increasing curve.

    Clamps outside the declared range — it never extrapolates. Extrapolating
    a normalisation curve is how a 400% ROE becomes a score of 340.
    """
    if not curve:
        raise ValueError("empty curve")
    if x <= curve[0][0]:
        return curve[0][1]
    if x >= curve[-1][0]:
        return curve[-1][1]
    for (x0, y0), (x1, y1) in zip(curve, curve[1:], strict=False):
        if x0 <= x <= x1:
            if x1 == x0:
                return y1
            weight = (x - x0) / (x1 - x0)
            return y0 + weight * (y1 - y0)
    return curve[-1][1]


def percentile_rank(value: float | None, population: list[float | None]) -> float | None:
    """Percentile of ``value`` within ``population``, 0-100.

    Uses the midpoint convention for ties, so a company equal to its only
    peer ranks at 50 rather than 0 or 100. The subject company's own value
    should be included in ``population``.
    """
    if value is None:
        return None
    present = [v for v in population if v is not None]
    if len(present) < 2:
        return None
    below = sum(1 for v in present if v < value)
    equal = sum(1 for v in present if v == value)
    return 100.0 * (below + 0.5 * equal) / len(present)
