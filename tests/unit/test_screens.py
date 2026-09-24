"""Composite screen tests: Piotroski, Altman, DuPont.

Each screen is asserted against its published definition, computed longhand.
Where the implementation deviates from the paper because of Indian reporting
practice, the test asserts that the deviation is *disclosed* — an undisclosed
deviation in a screen that carries an academic's name is a methodological
failure, not a rounding difference.
"""

from __future__ import annotations

import pytest

from app.engine.screens import altman_z_score, dupont_decomposition, piotroski_f_score

pytestmark = pytest.mark.golden


# ------------------------------------------------------------- Piotroski ----
def test_piotroski_all_nine_signals_pass(acme, acme_metrics):
    """Acme was built to pass every signal, exercising all nine branches."""
    screen = acme_metrics.screens["piotroski_f_score"]
    assert screen.score == 9.0
    assert screen.max_score == 9.0
    for signal, passed in screen.components.items():
        assert passed is True, f"signal '{signal}' did not pass"
    assert "9/9" in screen.interpretation


def test_piotroski_uses_beginning_of_year_assets(acme):
    """Faithful to Piotroski (2000), and the deviation that remains is stated."""
    statements = acme.statements
    screen = piotroski_f_score(statements.annual[0], statements.annual[1], statements.annual[2])
    assert "beginning-of-year total assets" in " ".join(screen.caveats)
    assert "average assets" in " ".join(screen.caveats)      # signal 5 deviation


def test_piotroski_distressed_company_fails_five_signals(levcyc_metrics):
    """Computed longhand from the fixture; only signals 1, 2, 5 and 7 pass.

    ROA fell (0.85% from 2.15%), OCF of 15 Cr is below PAT of 33 Cr, the
    current ratio slipped, gross margin compressed and asset turnover fell.
    """
    screen = levcyc_metrics.screens["piotroski_f_score"]
    assert screen.score == 4.0

    assert screen.components["roa_positive"] is True
    assert screen.components["ocf_positive"] is True
    assert screen.components["leverage_fell"] is True
    assert screen.components["no_new_shares"] is True

    assert screen.components["roa_improved"] is False
    assert screen.components["ocf_exceeds_pat"] is False
    assert screen.components["current_ratio_improved"] is False
    assert screen.components["gross_margin_improved"] is False
    assert screen.components["asset_turnover_improved"] is False


def test_piotroski_needs_two_years(acme):
    screen = piotroski_f_score(acme.statements.annual[0], None)
    assert screen.score is None
    assert "two consecutive annual periods" in screen.interpretation


def test_piotroski_flags_a_two_year_score_as_a_floor(acme):
    """Without a third year, two signals cannot be evaluated.

    They score zero by construction, so the result is a lower bound rather
    than a measurement — and the caveat has to say so, otherwise a 7/9 that
    is really an unmeasurable 9/9 reads as a mediocre company.
    """
    screen = piotroski_f_score(acme.statements.annual[0], acme.statements.annual[1], None)
    assert screen.score == 7.0
    assert "floor, not a measurement" in " ".join(screen.caveats)


# ----------------------------------------------------------------- Altman ---
def test_altman_original_variant_longhand(acme, acme_metrics):
    """Z = 1.2 X1 + 1.4 X2 + 3.3 X3 + 0.6 X4 + 1.0 X5, market-value variant."""
    x1 = (3800.0 - 1800.0) / 9000.0        # working capital / total assets
    x2 = 4000.0 / 9000.0                   # reserves (proxy for retained) / TA
    x3 = 2000.0 / 9000.0                   # EBIT / TA
    x4 = 15000.0 / 4000.0                  # market cap / total liabilities
    x5 = 10000.0 / 9000.0                  # revenue / TA
    expected = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5

    screen = acme_metrics.screens["altman_z_score"]
    assert screen.score == pytest.approx(expected, rel=1e-9)
    assert screen.score == pytest.approx(4.98333333, rel=1e-8)
    assert "SAFE" in screen.interpretation
    assert screen.variant == "Altman Z (1968), listed non-financial"
    assert "Reserves & surplus used as a proxy" in " ".join(screen.caveats)


def test_altman_distress_zone_longhand(levcyc_metrics):
    """The distressed fixture lands at about 1.14, below the 1.81 line."""
    x1 = (1800.0 - 1700.0) / 4000.0
    x2 = 300.0 / 4000.0
    x3 = 150.0 / 4000.0
    x4 = 750.0 / 3400.0
    x5 = 3000.0 / 4000.0
    expected = 1.2 * x1 + 1.4 * x2 + 3.3 * x3 + 0.6 * x4 + 1.0 * x5

    screen = levcyc_metrics.screens["altman_z_score"]
    assert screen.score == pytest.approx(expected, rel=1e-9)
    assert screen.score == pytest.approx(1.14110294, rel=1e-8)
    assert screen.score < 1.81
    assert "DISTRESS" in screen.interpretation
    assert "V4" in screen.interpretation


def test_altman_switches_to_private_variant_without_a_price(acme):
    """Comparing a Z' against Z thresholds is a silent, serious error.

    Without a market price the coefficients *and* the zone boundaries both
    change. The test asserts the variant switch is recorded, so a reader
    cannot mistake one for the other.
    """
    screen = altman_z_score(acme.statements.latest, market=None)
    assert screen.variant == "Altman Z' (private-firm variant)"
    assert "book equity substituted" in " ".join(screen.caveats)

    x1 = (3800.0 - 1800.0) / 9000.0
    x2 = 4000.0 / 9000.0
    x3 = 2000.0 / 9000.0
    x4 = 5000.0 / 4000.0                   # book equity, not market cap
    x5 = 10000.0 / 9000.0
    expected = 0.717 * x1 + 0.847 * x2 + 3.107 * x3 + 0.420 * x4 + 0.998 * x5
    assert screen.score == pytest.approx(expected, rel=1e-9)
    assert "2.9" in screen.interpretation   # the Z' safe threshold, not 2.99


# ----------------------------------------------------------------- DuPont ---
def test_dupont_identity_holds_exactly(acme, acme_metrics):
    """The product of the three factors must equal the reported ROE.

    This is the test that justifies calling it a decomposition. All three
    factors are computed on an average balance-sheet basis precisely so that
    the identity closes; a mixed basis would leave a residual and the
    "ROE is driven by X" claim would not follow from the arithmetic.
    """
    screen = acme_metrics.screens["dupont"]
    components = screen.components

    net_margin = components["net_margin"] / 100.0
    turnover = components["asset_turnover"]
    multiplier = components["equity_multiplier"]

    reconstructed = net_margin * turnover * multiplier * 100.0
    reported_roe = acme_metrics.value("roe")

    assert reconstructed == pytest.approx(reported_roe, abs=1e-9)
    assert screen.score == pytest.approx(reported_roe, abs=1e-9)
    assert components["roe_reconstructed"] == pytest.approx(reported_roe, abs=1e-9)


def test_dupont_factors_longhand(acme_metrics):
    avg_assets = (9000.0 + 7600.0) / 2
    avg_equity = (5000.0 + 3800.0) / 2
    components = acme_metrics.screens["dupont"].components

    assert components["net_margin"] == pytest.approx(1425.0 / 10000.0 * 100, rel=1e-9)
    assert components["asset_turnover"] == pytest.approx(10000.0 / avg_assets, rel=1e-9)
    assert components["equity_multiplier"] == pytest.approx(avg_assets / avg_equity, rel=1e-9)
    assert components["avg_total_assets"] == pytest.approx(avg_assets)
    assert components["avg_equity"] == pytest.approx(avg_equity)


def test_dupont_names_the_dominant_driver(acme_metrics, levcyc_metrics):
    """The sentence the narrative agent will reuse.

    Acme's 1.89x multiplier is unremarkable, so its ROE is not
    leverage-driven. The distressed company's 6.7x multiplier is, and saying
    so is the analytically useful part — two companies at the same ROE with
    those multipliers are not comparable businesses.
    """
    assert "Leverage-driven" not in acme_metrics.screens["dupont"].interpretation
    assert "Leverage-driven" in levcyc_metrics.screens["dupont"].interpretation


def test_dupont_falls_back_to_closing_values_and_says_so(acme):
    screen = dupont_decomposition(acme.statements.latest, prev=None)
    assert "closing values used" in " ".join(screen.caveats).lower()


# ----------------------------------------------- cash conversion streak -----
def test_conversion_streak_boundary(levcyc_metrics):
    """Exactly three consecutive sub-0.5 years: the V3 trigger boundary.

    FY2026 0.45, FY2025 0.37, FY2024 0.39 all fail; FY2023 at 1.06 breaks the
    streak. One weak year is ordinary; three in a row is the pattern the veto
    responds to.
    """
    screen = levcyc_metrics.screens["cash_conversion_streak"]
    assert screen.score == 3.0
    assert "V3" in screen.interpretation

    assert screen.components["ocf_to_pat_FY2026"] == pytest.approx(15.0 / 33.0)
    assert screen.components["ocf_to_pat_FY2025"] == pytest.approx(30.0 / 82.0)
    assert screen.components["ocf_to_pat_FY2024"] == pytest.approx(60.0 / 155.0)
    assert screen.components["ocf_to_pat_FY2023"] == pytest.approx(210.0 / 198.0)


def test_conversion_streak_zero_for_healthy_company(acme_metrics):
    screen = acme_metrics.screens["cash_conversion_streak"]
    assert screen.score == 0.0
    assert "V3" not in screen.interpretation
