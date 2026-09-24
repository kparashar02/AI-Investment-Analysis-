"""Arithmetic primitives.

The recurring theme: a missing input must produce a missing result, never a
zero. Substituting zero for "we don't know" is how a data gap becomes a
confidently wrong recommendation, and every one of these guards exists to
stop that at the lowest possible level.
"""

from __future__ import annotations

import pytest

from app.engine.helpers import (
    average,
    clamp,
    interpolate,
    mean,
    percentile_rank,
    pstdev,
    safe_div,
)


class TestSafeDiv:
    def test_normal(self):
        assert safe_div(10, 4) == pytest.approx(2.5)

    @pytest.mark.parametrize(
        ("numerator", "denominator"),
        [(10, 0), (10, None), (None, 4), (None, None), (0, 0)],
    )
    def test_returns_none_never_zero(self, numerator, denominator):
        """Zero would be indistinguishable from a genuine zero result."""
        assert safe_div(numerator, denominator) is None

    def test_zero_numerator_is_a_real_zero(self):
        assert safe_div(0, 4) == 0.0


class TestAverage:
    def test_two_points(self):
        assert average(100, 80) == pytest.approx(90.0)

    def test_falls_back_to_the_single_available_value(self):
        """A first-year company has no opening balance sheet.

        Losing ROE entirely would be worse than computing it on closing
        equity; the caller records the fallback on the metric.
        """
        assert average(100, None) == pytest.approx(100.0)

    def test_all_missing(self):
        assert average(None, None) is None


class TestStatistics:
    def test_population_sigma(self):
        """Divisor n, not n-1. The choice changes growth_consistency."""
        values = [2.0, 4.0, 4.0, 4.0, 5.0, 5.0, 7.0, 9.0]
        assert mean(values) == pytest.approx(5.0)
        assert pstdev(values) == pytest.approx(2.0)      # sample sigma would be 2.138

    def test_sigma_needs_two_points(self):
        assert pstdev([5.0]) is None
        assert pstdev([]) is None

    def test_mean_of_empty(self):
        assert mean([]) is None


class TestInterpolate:
    CURVE = [(0.0, 0.0), (10.0, 50.0), (20.0, 100.0)]

    def test_at_breakpoints(self):
        assert interpolate(self.CURVE, 0.0) == pytest.approx(0.0)
        assert interpolate(self.CURVE, 10.0) == pytest.approx(50.0)
        assert interpolate(self.CURVE, 20.0) == pytest.approx(100.0)

    def test_between_breakpoints(self):
        assert interpolate(self.CURVE, 5.0) == pytest.approx(25.0)
        assert interpolate(self.CURVE, 15.0) == pytest.approx(75.0)

    def test_clamps_and_never_extrapolates(self):
        """Extrapolating a normalisation curve is how a 400% ROE scores 340."""
        assert interpolate(self.CURVE, -100.0) == pytest.approx(0.0)
        assert interpolate(self.CURVE, 1e9) == pytest.approx(100.0)

    def test_decreasing_curve_encodes_lower_is_better(self):
        """No direction flag is needed: the curve itself carries the direction."""
        curve = [(0.0, 100.0), (1.0, 70.0), (3.0, 20.0)]
        assert interpolate(curve, 0.5) == pytest.approx(85.0)
        assert interpolate(curve, 2.0) == pytest.approx(45.0)

    def test_hump_curve(self):
        """Used for capex intensity, where both extremes are unattractive."""
        curve = [(0.0, 60.0), (5.0, 90.0), (20.0, 20.0)]
        assert interpolate(curve, 5.0) == pytest.approx(90.0)
        assert interpolate(curve, 0.0) < interpolate(curve, 5.0)
        assert interpolate(curve, 20.0) < interpolate(curve, 5.0)

    def test_empty_curve_raises(self):
        with pytest.raises(ValueError):
            interpolate([], 1.0)


class TestPercentileRank:
    def test_midpoint_convention_for_ties(self):
        """A company equal to its only peer ranks at 50, not 0 or 100."""
        assert percentile_rank(10.0, [10.0, 10.0]) == pytest.approx(50.0)

    def test_ordering(self):
        population = [1.0, 2.0, 3.0, 4.0]
        assert percentile_rank(1.0, population) == pytest.approx(12.5)
        assert percentile_rank(4.0, population) == pytest.approx(87.5)

    def test_needs_a_population(self):
        assert percentile_rank(5.0, [5.0]) is None
        assert percentile_rank(None, [1.0, 2.0]) is None

    def test_ignores_missing_peers(self):
        assert percentile_rank(3.0, [1.0, None, 5.0]) == pytest.approx(50.0)


def test_clamp():
    assert clamp(5.0, 0.0, 1.0) == 1.0
    assert clamp(-5.0, 0.0, 1.0) == 0.0
    assert clamp(0.5, 0.0, 1.0) == 0.5
