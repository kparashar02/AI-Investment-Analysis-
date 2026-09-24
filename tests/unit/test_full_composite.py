"""The seven-pillar composite (app/engine/scoring.build_decision with agents).

When the news/industry/risk agent outputs and a peer set are supplied, the
QUANT_CORE partial rating of Phase 2 becomes the FULL seven-pillar decision.
All agent outputs are hand-built bounded structures — no LLM, no network.
"""

from __future__ import annotations

import pytest

from app.engine.scoring import build_decision
from app.models.agent_io import (
    ArticleAssessment,
    FinancialImpact,
    GovernanceSeverity,
    IndustryAssessment,
    NewsAnalysis,
    Relevance,
    RiskAssessment,
)
from app.models.decision import Rating, RatingBasis


def _news(impact=FinancialImpact.POSITIVE) -> NewsAnalysis:
    return NewsAnalysis(as_of="2026-03-31", articles=[
        ArticleAssessment(title="a", relevance=Relevance.HIGH, source_tier=1,
                          financial_impact=impact, materiality=4, confidence=0.9,
                          published_date="2026-03-31"),
    ])


def _industry() -> IndustryAssessment:
    return IndustryAssessment(industry_growth_outlook=4, competitive_intensity=2,
                              regulatory_risk=2, cyclicality=3)


def _risk(governance=GovernanceSeverity.NONE) -> RiskAssessment:
    return RiskAssessment(financial_risk=25, business_risk=30, valuation_risk=35,
                          governance_flags=15, governance_severity=governance)


# A peer set cheaper than acme, so the valuation pillar also becomes scorable.
_PEERS = {"pe": [8.0, 9.0, 10.0, 11.0], "ev_ebitda": [7.0, 8.0, 9.0, 10.0]}


def test_all_seven_pillars_make_a_full_decision(acme, acme_metrics):
    decision = build_decision(
        acme_metrics, acme.statements, acme.market,
        peer_multiples=_PEERS, news=_news(), industry=_industry(), risk=_risk())
    assert decision.basis is RatingBasis.FULL
    assert len(decision.pillars_scored) == 7
    assert decision.pillars_missing == []
    assert decision.weight_covered == pytest.approx(1.0)
    assert decision.composite_score is not None


def test_full_decision_reaches_high_confidence(acme, acme_metrics):
    decision = build_decision(
        acme_metrics, acme.statements, acme.market,
        peer_multiples=_PEERS, news=_news(), industry=_industry(), risk=_risk())
    from app.models.decision import Confidence
    assert decision.confidence is Confidence.HIGH   # full coverage, DCF at BASE


def test_risk_pillar_enters_inverted(acme, acme_metrics):
    # A high-risk assessment must lower the composite versus a low-risk one.
    low = build_decision(acme_metrics, acme.statements, acme.market, peer_multiples=_PEERS,
                         news=_news(), industry=_industry(),
                         risk=RiskAssessment(financial_risk=10, business_risk=10,
                                             valuation_risk=10, governance_flags=10))
    high = build_decision(acme_metrics, acme.statements, acme.market, peer_multiples=_PEERS,
                          news=_news(), industry=_industry(),
                          risk=RiskAssessment(financial_risk=90, business_risk=90,
                                              valuation_risk=90, governance_flags=90))
    assert high.composite_score < low.composite_score


def test_negative_news_lowers_composite(acme, acme_metrics):
    good = build_decision(acme_metrics, acme.statements, acme.market, peer_multiples=_PEERS,
                          news=_news(FinancialImpact.POSITIVE), industry=_industry(), risk=_risk())
    bad = build_decision(acme_metrics, acme.statements, acme.market, peer_multiples=_PEERS,
                         news=_news(FinancialImpact.NEGATIVE), industry=_industry(), risk=_risk())
    assert bad.composite_score < good.composite_score


def test_governance_high_triggers_v6_and_caps_rating(acme, acme_metrics):
    decision = build_decision(
        acme_metrics, acme.statements, acme.market, peer_multiples=_PEERS,
        news=_news(), industry=_industry(), risk=_risk(GovernanceSeverity.HIGH))
    assert "V6" in decision.applied_vetoes
    assert decision.rating.rank >= Rating.HOLD.rank   # capped no better than HOLD


def test_v6_not_evaluable_without_risk_agent(acme, acme_metrics):
    decision = build_decision(acme_metrics, acme.statements, acme.market, peer_multiples=_PEERS)
    v6 = next(v for v in decision.vetoes if v.code == "V6")
    assert v6.evaluable is False


@pytest.mark.determinism
def test_full_decision_is_deterministic(acme, acme_metrics):
    fingerprints = {
        build_decision(acme_metrics, acme.statements, acme.market, peer_multiples=_PEERS,
                       news=_news(), industry=_industry(), risk=_risk()).fingerprint()
        for _ in range(5)
    }
    assert len(fingerprints) == 1
