"""Scoring of agent outputs into pillars (PRD 12.3).

The news, industry and risk pillars are the ones an LLM contributes to — and
this module is where the model's contribution stops being linguistic and
becomes a number. The agents emit bounded fields (a 1-5 materiality, a
three-way sentiment); every arithmetic step that turns those into a 0-100
pillar score happens here, in pure Python, deterministically. This is PRD §6
applied at the finest granularity in the system.

The module lives in ``app/engine`` because it is calculation, not judgement,
and it imports only the shared agent-output models — never an agent, never an
LLM client — so the engine stays the deterministic layer the layering test
guards.
"""

from __future__ import annotations

import math
from datetime import date
from typing import Any

from app.config.settings import load_weights
from app.models.agent_io import (
    IndustryAssessment,
    NewsAggregate,
    NewsAnalysis,
    RiskAssessment,
)

NEUTRAL_SCORE = 50.0


# ---------------------------------------------------------------- news -------

def _parse_date(value: str | None) -> date | None:
    if not value:
        return None
    try:
        return date.fromisoformat(value[:10])
    except ValueError:
        return None


def _article_age_days(published: str | None, as_of: date | None) -> int:
    """Whole days between publication and the as-of date, floored at 0."""
    pub = _parse_date(published)
    if pub is None or as_of is None:
        return 0
    return max(0, (as_of - pub).days)


def score_news(
    analysis: NewsAnalysis,
    weights: dict[str, Any] | None = None,
    *,
    as_of: str | None = None,
) -> NewsAggregate:
    """Compute the news pillar from per-article assessments (PRD 12.3, 8.6).

    ``weight_i = materiality × credibility × confidence × exp(-age/halflife)``,
    ``news_raw = Σ(direction·weight) / Σweight ∈ [-1, 1]``,
    ``news_score = 50 + 50·news_raw``.

    Below the configured minimum total weight the score is neutral 50 with a
    flag, because a couple of stale or low-conviction items are not evidence
    enough to move a fundamental process (PRD 8.6). The Alpha Vantage
    sentiment score is never an input here — the number is the agent's own
    reasoning, computed.
    """
    weights = weights or load_weights()
    news_cfg = weights.get("news_scoring") or {}
    halflife = float(news_cfg.get("recency_halflife_days", 30))
    min_weight = float(news_cfg.get("min_weight_threshold", 2.0))
    credibility_by_tier = news_cfg.get("source_tier_credibility") or {1: 1.0, 2: 0.6, 3: 0.25}

    as_of_date = _parse_date(as_of or analysis.as_of)

    considered = len(analysis.articles)
    total_weight = 0.0
    weighted_direction = 0.0
    scored = 0
    for article in analysis.articles:
        if not article.scored:                      # relevance gate (LOW/NONE dropped)
            continue
        scored += 1
        # Credibility is derived from the source tier in config, never taken
        # from any weight the model may have emitted — the multiplier is ours.
        credibility = float(credibility_by_tier.get(article.source_tier,
                             credibility_by_tier.get(str(article.source_tier), 0.25)))
        age = _article_age_days(article.published_date, as_of_date)
        recency = math.exp(-age / halflife) if halflife > 0 else 1.0
        weight = article.materiality * credibility * article.confidence * recency
        total_weight += weight
        weighted_direction += article.financial_impact.direction * weight

    notes: list[str] = []
    if total_weight < min_weight:
        notes.append(
            f"Total news weight {total_weight:.2f} is below the {min_weight} threshold; "
            f"news scored neutral (50) — insufficient coverage to move the case."
        )
        return NewsAggregate(
            news_score=NEUTRAL_SCORE, articles_considered=considered, articles_scored=scored,
            total_weight=round(total_weight, 4), insufficient_coverage=True, notes=notes,
        )

    news_raw = weighted_direction / total_weight    # in [-1, 1]
    news_score = 50.0 + 50.0 * news_raw
    return NewsAggregate(
        news_score=round(news_score, 2), articles_considered=considered, articles_scored=scored,
        total_weight=round(total_weight, 4), insufficient_coverage=False, notes=notes,
    )


def news_pillar(
    analysis: NewsAnalysis, weights: dict[str, Any] | None = None, *, as_of: str | None = None,
) -> tuple[float, dict[str, float], list[str]]:
    """News pillar score, plus a component breakdown and notes for the report."""
    aggregate = score_news(analysis, weights, as_of=as_of)
    analysis.aggregate = aggregate
    components = {
        "materiality_weighted_direction": round(aggregate.news_score, 2),
    }
    return aggregate.news_score, components, list(aggregate.notes)


# ------------------------------------------------------------ industry -------

def _map_up(value: int) -> float:
    """1-5 where higher is better → 0-100."""
    return (value - 1) / 4.0 * 100.0


def _map_down(value: int) -> float:
    """1-5 where higher is worse → 0-100 (inverted)."""
    return (5 - value) / 4.0 * 100.0


def score_industry(
    assessment: IndustryAssessment, weights: dict[str, Any] | None = None,
) -> tuple[float, dict[str, float], list[str]]:
    """Map the four 1-5 drivers onto a 0-100 industry pillar (PRD 12.3).

    Growth outlook points up (higher is better); competitive intensity,
    regulatory risk and cyclicality point down (higher is worse) and are
    inverted before weighting. Component weights come from weights.yaml.
    """
    weights = weights or load_weights()
    comp_weights = (weights.get("components") or {}).get("industry") or {}

    mapped = {
        "growth_outlook": _map_up(assessment.industry_growth_outlook),
        "competitive_intensity": _map_down(assessment.competitive_intensity),
        "regulatory_risk": _map_down(assessment.regulatory_risk),
        "cyclicality": _map_down(assessment.cyclicality),
    }
    score = sum(float(comp_weights.get(name, 0.0)) * value for name, value in mapped.items())
    contributions = {name: round(float(comp_weights.get(name, 0.0)) * value, 2)
                     for name, value in mapped.items()}
    return round(score, 2), contributions, list(assessment.notes)


# --------------------------------------------------------------- risk --------

def score_risk(
    assessment: RiskAssessment, weights: dict[str, Any] | None = None,
) -> tuple[float, dict[str, float], list[str]]:
    """Weighted risk *level* 0-100 (PRD 12.3), higher = riskier.

    Returned as the risk level; the composite inverts it (100 - risk) so more
    risk lowers the score. Its principal protective role is still the vetoes,
    not this 5% marginal weight (PRD 12.1)."""
    weights = weights or load_weights()
    comp_weights = (weights.get("components") or {}).get("risk") or {}

    values = {
        "financial_risk": assessment.financial_risk,
        "business_risk": assessment.business_risk,
        "valuation_risk": assessment.valuation_risk,
        "governance_flags": assessment.governance_flags,
    }
    score = sum(float(comp_weights.get(name, 0.0)) * value for name, value in values.items())
    contributions = {name: round(float(comp_weights.get(name, 0.0)) * value, 2)
                     for name, value in values.items()}
    return round(score, 2), contributions, list(assessment.notes)
