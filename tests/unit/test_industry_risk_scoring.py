"""Industry and risk pillar scoring (app/engine/agent_scoring)."""

from __future__ import annotations

import pytest

from app.engine.agent_scoring import score_industry, score_risk
from app.models.agent_io import GovernanceSeverity, IndustryAssessment, RiskAssessment


# --- industry --------------------------------------------------------------

def test_industry_hand_computed():
    # growth 4 -> up 75 (*0.35=26.25); intensity 2 -> down 75 (*0.25=18.75);
    # reg 2 -> down 75 (*0.20=15); cyclicality 3 -> down 50 (*0.20=10). Sum 70.0.
    assessment = IndustryAssessment(
        industry_growth_outlook=4, competitive_intensity=2, regulatory_risk=2, cyclicality=3)
    score, contributions, _ = score_industry(assessment)
    assert score == pytest.approx(70.0)
    assert contributions["growth_outlook"] == pytest.approx(26.25)
    assert contributions["cyclicality"] == pytest.approx(10.0)


def test_industry_best_case_is_100():
    best = IndustryAssessment(
        industry_growth_outlook=5, competitive_intensity=1, regulatory_risk=1, cyclicality=1)
    score, _, _ = score_industry(best)
    assert score == pytest.approx(100.0)


def test_industry_worst_case_is_zero():
    worst = IndustryAssessment(
        industry_growth_outlook=1, competitive_intensity=5, regulatory_risk=5, cyclicality=5)
    score, _, _ = score_industry(worst)
    assert score == pytest.approx(0.0)


def test_industry_direction_of_drivers():
    # Raising competitive intensity (bad) must lower the score.
    calm = score_industry(IndustryAssessment(
        industry_growth_outlook=3, competitive_intensity=1, regulatory_risk=3, cyclicality=3))[0]
    fierce = score_industry(IndustryAssessment(
        industry_growth_outlook=3, competitive_intensity=5, regulatory_risk=3, cyclicality=3))[0]
    assert fierce < calm


# --- risk ------------------------------------------------------------------

def test_risk_weighted_level():
    # 0.40*30 + 0.30*40 + 0.20*50 + 0.10*20 = 12 + 12 + 10 + 2 = 36.
    assessment = RiskAssessment(
        financial_risk=30, business_risk=40, valuation_risk=50, governance_flags=20)
    score, contributions, _ = score_risk(assessment)
    assert score == pytest.approx(36.0)
    assert contributions["financial_risk"] == pytest.approx(12.0)


def test_risk_is_a_level_not_inverted_here():
    # score_risk returns the risk LEVEL; inversion happens in the composite.
    high = score_risk(RiskAssessment(
        financial_risk=90, business_risk=90, valuation_risk=90, governance_flags=90))[0]
    assert high == pytest.approx(90.0)


def test_governance_severity_is_carried_for_v6():
    assessment = RiskAssessment(
        financial_risk=10, business_risk=10, valuation_risk=10, governance_flags=80,
        governance_severity=GovernanceSeverity.HIGH)
    assert assessment.governance_severity is GovernanceSeverity.HIGH


def test_bounded_fields_reject_out_of_range():
    import pydantic

    with pytest.raises(pydantic.ValidationError):
        RiskAssessment(financial_risk=150, business_risk=10, valuation_risk=10, governance_flags=10)
    with pytest.raises(pydantic.ValidationError):
        IndustryAssessment(industry_growth_outlook=6, competitive_intensity=1,
                           regulatory_risk=1, cyclicality=1)
