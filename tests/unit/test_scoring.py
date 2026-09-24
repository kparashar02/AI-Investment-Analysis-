"""The decision engine (app/engine/scoring.py) — composite, bands, decision."""

from __future__ import annotations

import pytest

from app.engine.scoring import build_decision, composite_score, rating_for_score
from app.models.decision import Rating, RatingBasis


# --- rating bands ----------------------------------------------------------

@pytest.mark.parametrize("score,expected", [
    (85, Rating.BUY), (78, Rating.BUY),          # lower edge of BUY
    (70, Rating.ACCUMULATE), (65, Rating.ACCUMULATE),
    (50, Rating.HOLD), (45, Rating.HOLD),
    (35, Rating.REDUCE), (30, Rating.REDUCE),
    (20, Rating.SELL), (0, Rating.SELL),
])
def test_rating_bands(score, expected):
    assert rating_for_score(score) is expected


# --- composite renormalisation ---------------------------------------------

def test_composite_renormalises_over_available_pillars():
    composite, covered, weights = composite_score(
        {"fundamentals": 80.0, "growth": 60.0, "cashflow": None,
         "valuation": None, "industry": None, "news": None, "risk": None})
    # 0.30 and 0.15 renormalise to 2/3 and 1/3 of the covered weight.
    assert covered == pytest.approx(0.45)
    assert composite == pytest.approx(80 * (0.30 / 0.45) + 60 * (0.15 / 0.45))


def test_risk_pillar_enters_inverted():
    composite, covered, _ = composite_score({"risk": 80.0})
    assert composite == pytest.approx(20.0)   # 100 - 80


def test_no_pillars_gives_no_composite():
    composite, covered, weights = composite_score({"fundamentals": None})
    assert composite is None
    assert covered == 0.0


# --- full decision on the fixtures -----------------------------------------

def test_healthy_company_is_a_buy(acme, acme_metrics):
    decision = build_decision(acme_metrics, acme.statements, acme.market)
    assert decision.rating is Rating.BUY
    assert decision.basis is RatingBasis.QUANT_CORE      # only the quant pillars
    assert decision.composite_score == pytest.approx(85.95, abs=0.5)
    assert "valuation" in decision.pillars_missing        # only 40% computable, no peers
    assert decision.weight_covered == pytest.approx(0.55, abs=0.001)
    assert decision.valuation.upside_pct > 0


def test_decision_discloses_missing_pillars_in_warnings(acme, acme_metrics):
    decision = build_decision(acme_metrics, acme.statements, acme.market)
    assert any("QUANT_CORE" in w and "renormalised" in w for w in decision.warnings)


def test_distressed_company_is_capped_to_sell(levcyc, levcyc_metrics):
    decision = build_decision(levcyc_metrics, levcyc.statements, levcyc.market)
    assert decision.rating is Rating.SELL
    # Its DCF upside is nonsensically large, but the vetoes govern the rating.
    assert set(decision.applied_vetoes) >= {"V2", "V4"}


def test_bfsi_is_not_supported(acme, acme_metrics):
    bfsi_statements = acme.statements.model_copy(update={"sector": "Banking", "industry": "Private Bank"})
    decision = build_decision(acme_metrics, bfsi_statements, acme.market)
    assert decision.rating is Rating.NOT_SUPPORTED
    assert decision.basis is RatingBasis.NOT_SUPPORTED
    assert decision.composite_score is None
    assert "V8" in decision.applied_vetoes


def test_no_rating_when_data_incomplete(acme, acme_metrics):
    # Force V1 by attaching a thin data-quality report to a copy of the statements.
    from app.models.statements import DataQualityReport

    thin = acme.statements.model_copy()
    thin.data_quality = DataQualityReport(completeness_pct=40.0, annual_periods_available=5)
    decision = build_decision(acme_metrics, thin, acme.market)
    assert decision.rating is Rating.NO_RATING
    assert "V1" in decision.applied_vetoes


# --- determinism (PRD 12.7) ------------------------------------------------

@pytest.mark.determinism
def test_decision_is_bit_identical_across_runs(acme, acme_metrics):
    fingerprints = {
        build_decision(acme_metrics, acme.statements, acme.market).fingerprint()
        for _ in range(5)
    }
    assert len(fingerprints) == 1


@pytest.mark.determinism
def test_composite_stable_across_runs(levcyc, levcyc_metrics):
    scores = {
        build_decision(levcyc_metrics, levcyc.statements, levcyc.market).composite_score
        for _ in range(5)
    }
    assert len(scores) == 1
