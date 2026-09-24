"""Growth metric tests, including the cases that most often go wrong.

The interesting tests here are not the happy paths — they are the guards:
CAGR off a negative base, growth on a company whose profit is shrinking, and
consistency on a series whose mean is near zero. Each of those produces
confident nonsense in a naive implementation.
"""

from __future__ import annotations

import math

import pytest

from app.engine.growth import compute_growth
from app.engine.helpers import cagr, consistency_score, yoy_growth

pytestmark = pytest.mark.golden


# --------------------------------------------------------------- Acme CAGRs --
def test_revenue_cagr_over_full_window(acme_metrics):
    """FY2022 5,000 Cr to FY2026 10,000 Cr over four compounding intervals."""
    expected = ((10000.0 / 5000.0) ** (1.0 / 4.0) - 1.0) * 100.0     # 18.9207%
    assert acme_metrics.value("revenue_cagr") == pytest.approx(expected, rel=1e-9)
    assert acme_metrics.value("revenue_cagr") == pytest.approx(18.920711500272, rel=1e-9)


def test_cagr_metric_discloses_its_window(acme_metrics):
    """The endpoints and interval count travel with the metric.

    This is the fix for the "5-year CAGR" ambiguity: a reader can see that
    the number covers FY2022 to FY2026 over 4 intervals and never has to
    guess which convention was used.
    """
    metric = acme_metrics.get("revenue_cagr")
    assert metric.label == "Revenue CAGR (FY2022 to FY2026, 4y)"
    assert metric.inputs_used["intervals"] == 4.0
    assert metric.inputs_used["span_years"] == 5.0
    assert metric.inputs_used["begin"] == 5000.0
    assert metric.inputs_used["end"] == 10000.0


def test_pat_and_eps_cagr(acme_metrics):
    expected_pat = ((1425.0 / 500.0) ** 0.25 - 1.0) * 100.0          # 29.9305%
    assert acme_metrics.value("pat_cagr") == pytest.approx(expected_pat, rel=1e-9)
    # Share count is constant across all five years, so EPS growth must equal
    # PAT growth exactly. If it does not, the share-count plumbing is wrong.
    assert acme_metrics.value("eps_cagr") == pytest.approx(expected_pat, rel=1e-9)


def test_ebitda_and_fcf_cagr(acme_metrics):
    assert acme_metrics.value("ebitda_cagr") == pytest.approx(
        ((2500.0 / 1050.0) ** 0.25 - 1.0) * 100.0, rel=1e-9)
    assert acme_metrics.value("fcf_cagr") == pytest.approx(
        ((1200.0 / 300.0) ** 0.25 - 1.0) * 100.0, rel=1e-9)          # 41.4214%


def test_three_interval_window(acme_metrics):
    expected = ((10000.0 / 6000.0) ** (1.0 / 3.0) - 1.0) * 100.0     # 18.5631%
    assert acme_metrics.value("revenue_cagr_3y") == pytest.approx(expected, rel=1e-9)
    assert acme_metrics.get("revenue_cagr_3y").inputs_used["intervals"] == 3.0


def test_latest_yoy(acme_metrics):
    assert acme_metrics.value("revenue_growth_yoy") == pytest.approx(25.0, rel=1e-9)
    assert acme_metrics.value("pat_growth_yoy") == pytest.approx(
        (1425.0 / 975.0 - 1.0) * 100.0, rel=1e-9)


# ------------------------------------------------------------- consistency --
def test_growth_consistency_longhand(acme_metrics):
    """Recomputed independently, including the population-sigma choice."""
    growths = [
        (6000.0 / 5000.0 - 1) * 100,
        (7000.0 / 6000.0 - 1) * 100,
        (8000.0 / 7000.0 - 1) * 100,
        (10000.0 / 8000.0 - 1) * 100,
    ]
    mu = sum(growths) / len(growths)
    sigma = math.sqrt(sum((g - mu) ** 2 for g in growths) / len(growths))
    expected = 100.0 * (1.0 - sigma / abs(mu))

    assert acme_metrics.value("growth_consistency") == pytest.approx(expected, rel=1e-9)
    assert acme_metrics.value("growth_consistency") == pytest.approx(78.824, abs=0.01)


def test_steady_growth_beats_volatile_growth_at_the_same_mean():
    """The belief the metric exists to encode.

    Both series average 15% growth. The steady one must score far higher —
    if it did not, the metric would be measuring nothing.
    """
    steady = [15.0, 15.0, 15.0, 15.0]
    volatile = [55.0, -20.0, 40.0, -15.0]
    assert abs(sum(steady) / 4 - sum(volatile) / 4) < 1e-9

    steady_score, _ = consistency_score(steady)
    volatile_score, _ = consistency_score(volatile)
    assert steady_score == pytest.approx(100.0)
    assert volatile_score == pytest.approx(0.0)          # clamped at the floor
    assert steady_score > volatile_score


def test_consistency_undefined_when_mean_growth_is_zero():
    score, reason = consistency_score([20.0, -20.0, 20.0, -20.0])
    assert score is None
    assert "coefficient of variation" in reason


# ------------------------------------------------------------ margin trend --
def test_ebitda_margin_trend_longhand(acme_metrics):
    """OLS slope over the five annual EBITDA margins, computed independently."""
    margins = [
        1050.0 / 5000.0 * 100,      # FY2022  21.0000%
        1330.0 / 6000.0 * 100,      # FY2023  22.1667%
        1560.0 / 7000.0 * 100,      # FY2024  22.2857%
        1840.0 / 8000.0 * 100,      # FY2025  23.0000%
        2500.0 / 10000.0 * 100,     # FY2026  25.0000%
    ]
    xs = list(range(len(margins)))
    mean_x = sum(xs) / len(xs)
    mean_y = sum(margins) / len(margins)
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, margins, strict=True))
    sxx = sum((x - mean_x) ** 2 for x in xs)
    expected = sxy / sxx

    assert acme_metrics.value("ebitda_margin_trend") == pytest.approx(expected, rel=1e-9)
    assert acme_metrics.value("ebitda_margin_trend") == pytest.approx(0.8833, abs=0.001)
    assert acme_metrics.value("ebitda_margin_trend") > 0   # margins expanding


def test_margin_trend_detects_compression(levcyc_metrics):
    """The distressed fixture's margins are falling; the slope must be negative.

    A level-only reading would show an 11% EBITDA margin and stop there. The
    slope is what says the business is deteriorating.
    """
    assert levcyc_metrics.value("ebitda_margin_trend") < 0


# ------------------------------------------------------ declining company ---
def test_negative_cagr_is_reported_not_suppressed(levcyc_metrics):
    """Revenue 3,500 to 3,000 over three intervals: about -5.0% a year."""
    expected = ((3000.0 / 3500.0) ** (1.0 / 3.0) - 1.0) * 100.0
    assert levcyc_metrics.value("revenue_cagr") == pytest.approx(expected, rel=1e-9)
    assert levcyc_metrics.value("revenue_cagr") < 0
    # It must still score, at the low end of the curve, rather than go missing.
    assert levcyc_metrics.score("revenue_cagr") is not None
    assert levcyc_metrics.score("revenue_cagr") < 30


# ------------------------------------------------------------- CAGR guards --
@pytest.mark.parametrize(
    ("begin", "end", "years", "fragment"),
    [
        (-100.0, 500.0, 4, "non-positive base"),
        (0.0, 500.0, 4, "non-positive base"),
        (500.0, -100.0, 4, "non-positive endpoint"),
        (500.0, 0.0, 4, "non-positive endpoint"),
        (None, 500.0, 4, "missing endpoint"),
        (100.0, 200.0, 0, "non-positive interval"),
    ],
)
def test_cagr_refuses_undefined_cases(begin, end, years, fragment):
    """A turnaround from a loss has no compound growth rate.

    Returning a number here — which several commercial screeners do — yields
    figures like "PAT CAGR 220%" for a company that merely stopped losing
    money. The guard is the whole point.
    """
    value, reason = cagr(begin, end, years)
    assert value is None
    assert fragment in reason


def test_cagr_happy_path_is_exact():
    value, reason = cagr(100.0, 200.0, 1)
    assert reason is None
    assert value == pytest.approx(100.0)
    value, _ = cagr(100.0, 400.0, 2)
    assert value == pytest.approx(100.0)     # doubling each year


def test_yoy_growth_skips_non_positive_bases():
    assert yoy_growth([100.0, 110.0]) == [pytest.approx(10.0)]
    assert yoy_growth([-50.0, 110.0]) == [None]
    assert yoy_growth([100.0, None, 121.0]) == [None, None]


# ----------------------------------------------------- short history guard ---
def test_growth_metrics_need_three_periods(acme):
    """Two years of data cannot support a growth rate, and must say so."""
    statements = acme.statements.model_copy(deep=True)
    statements.annual = statements.annual[:2]

    metrics = compute_growth(statements)
    assert metrics["revenue_cagr"].value is None
    assert "at least 3 annual periods" in metrics["revenue_cagr"].unavailable_reason
    assert metrics["revenue_growth_yoy"].value is not None   # one interval is fine
