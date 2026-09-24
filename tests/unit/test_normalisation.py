"""Normalisation: raw value to 0-100 score, and pillar assembly.

This is the narrowest place in the system where a financial fact becomes a
judgement, so the tests here are about making that step behave predictably:
the right curve is chosen, sector overrides actually apply, a missing metric
redistributes weight rather than scoring zero, and a mostly-missing pillar
declines to report a score at all.
"""

from __future__ import annotations

import pytest

from app.engine.normalisation import (
    band_score,
    blend_scores,
    is_bfsi,
    pillar_score,
    sector_key,
)
from app.models.metrics import MetricValue, Pillar, Unit


# ------------------------------------------------------------- sector keys ---
@pytest.mark.parametrize(
    ("sector", "industry", "expected"),
    [
        ("Information Technology", "IT Services", "it_services"),
        ("Technology", None, "it_services"),
        ("Healthcare", "Pharmaceuticals", "pharmaceuticals"),
        ("Consumer Staples", "Household Products", "fmcg"),
        ("Capital Goods", "Industrial Machinery", "capital_goods"),
        ("Metals & Mining", "Steel", "metals_mining"),
        ("Utilities", "Power Generation", "utilities"),
        ("Something Unmapped", None, "general"),
        (None, None, "general"),
    ],
)
def test_sector_key_mapping(sector, industry, expected):
    assert sector_key(sector, industry) == expected


@pytest.mark.parametrize(
    ("sector", "industry", "expected"),
    [
        ("Financials", "Private Sector Bank", True),
        ("Financial Services", "NBFC", True),
        ("Financials", "Life Insurance", True),
        ("Financials", "Housing Finance", True),
        ("Information Technology", "IT Services", False),
        ("Capital Goods", "Industrial Machinery", False),
    ],
)
def test_bfsi_detection(sector, industry, expected):
    """Veto V8 depends on this: an FCFF-DCF is invalid for a lender.

    Detecting the sector is what lets the system decline to rate rather than
    produce a confidently invalid valuation.
    """
    assert is_bfsi(sector, industry) is expected


# ------------------------------------------------------------ band scoring ---
def test_roe_band_score_longhand(acme_metrics):
    """Acme's 32.39% ROE against the general curve.

    Curve: (0,0) (8,30) (15,55) (20,75) (30,92) (50,100). The value sits in
    the 30-50 segment, so the score is 92 + (32.386-30)/20 x 8.
    """
    roe = acme_metrics.value("roe")
    expected = 92.0 + (roe - 30.0) / (50.0 - 30.0) * (100.0 - 92.0)
    assert acme_metrics.score("roe") == pytest.approx(expected, rel=1e-9)
    assert acme_metrics.score("roe") == pytest.approx(92.95, abs=0.01)


def test_lower_is_better_metric_scores_high_when_low(acme_metrics):
    """D/E of 0.40 sits in the 0.30-0.70 segment: 90 + (0.10/0.40) x (70-90)."""
    expected = 90.0 + (0.40 - 0.30) / (0.70 - 0.30) * (70.0 - 90.0)
    assert acme_metrics.score("debt_to_equity") == pytest.approx(expected, rel=1e-9)
    assert acme_metrics.score("debt_to_equity") == pytest.approx(85.0, rel=1e-9)


def test_distressed_company_scores_low_on_leverage(levcyc_metrics):
    assert levcyc_metrics.score("debt_to_equity") < 20
    assert levcyc_metrics.score("interest_coverage") < 20
    assert levcyc_metrics.score("altman_z_score") < 25


def test_sector_override_applies_and_is_disclosed(acme_metrics):
    """Acme is Capital Goods, which overrides the capex-intensity curve.

    7% capex intensity scores 80 on the general curve and 86 on the
    capital-goods curve, because heavy capex is normal in that sector. A
    reader must be able to see which standard the company was judged
    against, so the override is recorded on the metric.
    """
    assert acme_metrics.sector_key == "capital_goods"

    general, _ = band_score("capex_intensity", 7.0, sector="general")
    sectoral, note = band_score("capex_intensity", 7.0, sector="capital_goods")

    assert general == pytest.approx(80.0, rel=1e-9)
    assert sectoral == pytest.approx(86.0, rel=1e-9)
    assert note is not None and "capital_goods" in note

    assert acme_metrics.score("capex_intensity") == pytest.approx(sectoral, rel=1e-9)
    assert any("capital_goods" in n for n in acme_metrics.get("capex_intensity").notes)


def test_unscored_metric_returns_none(acme_metrics):
    """Some metrics are computed for display but deliberately not scored.

    Net working capital in crore has no sensible universal curve — the number
    is meaningful only relative to revenue, which is a separate metric.
    """
    metric = acme_metrics.get("net_working_capital")
    assert metric.value is not None
    assert metric.final_score is None


def test_missing_value_scores_none_not_zero():
    score, _ = band_score("roe", None)
    assert score is None


def test_unknown_metric_is_not_scored():
    score, _ = band_score("a_metric_with_no_curve", 42.0)
    assert score is None


# ----------------------------------------------------------------- blending --
def test_blend_uses_configured_weights():
    """60% absolute standard, 40% peer standing."""
    assert blend_scores(80.0, 40.0) == pytest.approx(0.6 * 80 + 0.4 * 40)


def test_blend_without_peers_leaves_the_band_score_undiluted(acme_metrics):
    """Phase 1 has no peer set; the band score must stand alone.

    Diluting it against a null percentile would drag every score toward zero
    and make Phase 1 output incomparable with Phase 3 output.
    """
    assert blend_scores(80.0, None) == pytest.approx(80.0)
    for metric in acme_metrics.metrics.values():
        if metric.band_score is not None:
            assert metric.peer_percentile is None
            assert metric.final_score == pytest.approx(metric.band_score)


def test_peer_values_produce_a_percentile(acme, acme_metrics):
    """Peer standing is blended in when a peer set is supplied (Phase 3).

    The population must include the subject company's own value, which is
    why it is taken from the already-computed metric rather than retyped —
    a literal that differs in the last decimal place would silently turn a
    tie into a strict inequality and move the percentile.
    """
    from app.engine.registry import compute_metric_set

    own_roe = acme_metrics.value("roe")
    peers = {"roe": [own_roe, 12.0, 18.0, 25.0, 9.0]}
    metric_set = compute_metric_set(acme.statements, acme.market, peer_values=peers)
    metric = metric_set.get("roe")

    # Best of five, midpoint convention for its own tie: (4 + 0.5) / 5 = 90%.
    assert metric.peer_percentile == pytest.approx(90.0)
    assert metric.final_score == pytest.approx(
        0.6 * metric.band_score + 0.4 * 90.0, rel=1e-9
    )


# ------------------------------------------------------------ pillar scores --
def _metric(name: str, score: float | None) -> MetricValue:
    return MetricValue(
        name=name, label=name, value=1.0, unit=Unit.PCT,
        pillar=Pillar.FUNDAMENTALS, band_score=score, final_score=score,
    )


def test_pillar_score_weighted_average():
    metrics = {
        "roe": _metric("roe", 100.0),
        "roce": _metric("roce", 100.0),
        "ebitda_margin": _metric("ebitda_margin", 50.0),
        "net_margin": _metric("net_margin", 50.0),
        "debt_to_equity": _metric("debt_to_equity", 0.0),
        "interest_coverage": _metric("interest_coverage", 0.0),
        "piotroski_f_score": _metric("piotroski_f_score", 0.0),
    }
    # 0.20x100 + 0.20x100 + 0.15x50 + 0.10x50 + 0 + 0 + 0 = 52.5
    score, contributions, notes = pillar_score(metrics, "fundamentals")
    assert score == pytest.approx(52.5)
    assert notes == []
    assert sum(contributions.values()) == pytest.approx(52.5)


def test_missing_component_redistributes_rather_than_scoring_zero():
    """A gap in a data provider's coverage is not a fundamental weakness.

    Scoring a missing metric as zero would punish the company for its
    provider's shortcomings. Here Piotroski (10% of the pillar) is absent, so
    the remaining 90% is renormalised and every survivor scores 80 — giving
    80, not 72.
    """
    metrics = {
        name: _metric(name, 80.0)
        for name in ("roe", "roce", "ebitda_margin", "net_margin",
                     "debt_to_equity", "interest_coverage")
    }
    metrics["piotroski_f_score"] = _metric("piotroski_f_score", None)

    score, _, notes = pillar_score(metrics, "fundamentals")
    assert score == pytest.approx(80.0)
    assert score != pytest.approx(72.0)
    assert any("piotroski_f_score unavailable" in n for n in notes)
    assert any("redistributed" in n for n in notes)


def test_mostly_missing_pillar_declines_to_score():
    """Below half the pillar weight, there is not enough left to call it a
    measurement, so the pillar returns None and says why."""
    metrics = {
        "roe": _metric("roe", 90.0),          # 20% of the pillar
        "roce": _metric("roce", None),
        "ebitda_margin": _metric("ebitda_margin", None),
        "net_margin": _metric("net_margin", None),
        "debt_to_equity": _metric("debt_to_equity", None),
        "interest_coverage": _metric("interest_coverage", None),
        "piotroski_f_score": _metric("piotroski_f_score", None),
    }
    score, contributions, notes = pillar_score(metrics, "fundamentals")
    assert score is None
    assert contributions == {}
    assert any("not scored" in n for n in notes)


def test_pillar_preview_covers_only_the_computable_pillars(acme_metrics):
    """Phase 1 must not synthesise a composite from three of seven pillars."""
    from app.engine.registry import pillar_preview

    preview = pillar_preview(acme_metrics)
    assert set(preview) == {"fundamentals", "growth", "cashflow"}
    for pillar, detail in preview.items():
        assert detail["score"] is not None, f"{pillar} should be computable"
        assert 0.0 <= detail["score"] <= 100.0


def test_healthy_company_outscores_distressed_company(acme_metrics, levcyc_metrics):
    """The end-to-end sanity check: the model must rank these two correctly.

    If it does not, no amount of correct arithmetic underneath matters.
    """
    from app.engine.registry import pillar_preview

    healthy = pillar_preview(acme_metrics)
    distressed = pillar_preview(levcyc_metrics)

    for pillar in ("fundamentals", "growth", "cashflow"):
        assert healthy[pillar]["score"] > distressed[pillar]["score"], (
            f"pillar '{pillar}': healthy {healthy[pillar]['score']:.1f} is not "
            f"above distressed {distressed[pillar]['score']:.1f}"
        )
