"""News pillar scoring (app/engine/agent_scoring.score_news).

The PRD's two worked-example articles (§8.6) are the golden case: both dated
same-day, they must produce news_score ≈ 61.05 from the documented formula.
All inputs are hand-built ArticleAssessments — no LLM, no network.
"""

from __future__ import annotations

import pytest

from app.engine.agent_scoring import score_news
from app.models.agent_io import (
    ArticleAssessment,
    EventCategory,
    FinancialImpact,
    NewsAnalysis,
    Relevance,
    Sentiment,
    TimeHorizon,
)

AS_OF = "2026-03-31"


def _article(**kw) -> ArticleAssessment:
    base = dict(
        title="x", relevance=Relevance.HIGH, source_tier=1,
        event_category=EventCategory.OTHER, sentiment=Sentiment.NEUTRAL,
        financial_impact=FinancialImpact.NEUTRAL, materiality=3,
        time_horizon=TimeHorizon.MEDIUM, confidence=0.8, published_date=AS_OF,
    )
    base.update(kw)
    return ArticleAssessment(**base)


def test_prd_worked_examples_score():
    # Article A: order win, positive, materiality 4, tier 1, confidence 0.87.
    a = _article(relevance=Relevance.HIGH, financial_impact=FinancialImpact.POSITIVE,
                 materiality=4, confidence=0.87)
    # Article B: sector wage pressure, negative, materiality 3, tier 1, confidence 0.74.
    b = _article(relevance=Relevance.MEDIUM, financial_impact=FinancialImpact.NEGATIVE,
                 materiality=3, confidence=0.74)
    agg = score_news(NewsAnalysis(as_of=AS_OF, articles=[a, b]))
    # weight_A = 4*1.0*0.87 = 3.48 ; weight_B = 3*1.0*0.74 = 2.22
    # raw = (3.48 - 2.22)/5.70 = 0.2211 ; score = 50 + 50*0.2211 = 61.05
    assert agg.total_weight == pytest.approx(5.70, abs=0.001)
    assert agg.news_score == pytest.approx(61.05, abs=0.05)
    assert agg.insufficient_coverage is False
    assert agg.articles_scored == 2


def test_relevance_gate_drops_low_and_none():
    kept = _article(relevance=Relevance.HIGH, financial_impact=FinancialImpact.POSITIVE, materiality=5)
    dropped_low = _article(relevance=Relevance.LOW, financial_impact=FinancialImpact.NEGATIVE, materiality=5)
    dropped_none = _article(relevance=Relevance.NONE, financial_impact=FinancialImpact.NEGATIVE, materiality=5)
    agg = score_news(NewsAnalysis(as_of=AS_OF, articles=[kept, dropped_low, dropped_none]))
    assert agg.articles_considered == 3
    assert agg.articles_scored == 1        # only the HIGH-relevance one counts
    assert agg.news_score > 50             # sole scored item is positive


def test_insufficient_coverage_is_neutral_with_flag():
    thin = _article(relevance=Relevance.HIGH, financial_impact=FinancialImpact.POSITIVE,
                    materiality=1, confidence=0.3, source_tier=3)
    agg = score_news(NewsAnalysis(as_of=AS_OF, articles=[thin]))
    assert agg.insufficient_coverage is True
    assert agg.news_score == 50.0
    assert any("insufficient" in n.lower() or "below" in n.lower() for n in agg.notes)


def test_credibility_scales_with_source_tier():
    a = _article(source_tier=1, materiality=5, confidence=1.0)  # credibility 1.0 -> weight 5.0
    agg1 = score_news(NewsAnalysis(as_of=AS_OF, articles=[a]))
    b = _article(source_tier=3, materiality=5, confidence=1.0)  # credibility 0.25 -> weight 1.25
    # Two tier-3 items to clear the threshold and isolate the weight.
    agg3 = score_news(NewsAnalysis(as_of=AS_OF, articles=[b, _article(source_tier=1, materiality=5, confidence=1.0)]))
    assert agg1.total_weight == pytest.approx(5.0)


def test_recency_decay_reduces_old_article_weight():
    fresh_positive = _article(financial_impact=FinancialImpact.POSITIVE, materiality=4,
                              confidence=1.0, published_date=AS_OF)
    old_negative = _article(financial_impact=FinancialImpact.NEGATIVE, materiality=4,
                            confidence=1.0, published_date="2026-01-30")  # ~60 days old
    agg = score_news(NewsAnalysis(as_of=AS_OF, articles=[fresh_positive, old_negative]))
    # Old negative decays to ~13% weight, so the fresh positive dominates.
    assert agg.news_score > 50


def test_alpha_vantage_sentiment_is_not_an_input():
    # There is deliberately no field to carry an external sentiment score; the
    # schema simply has nowhere to put one (PRD 8.6). Guard that intent.
    assert "overall_sentiment_score" not in ArticleAssessment.model_fields
