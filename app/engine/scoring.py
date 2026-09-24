"""The decision engine (PRD 12) — pure Python, fully deterministic, no LLM.

Turns the scored pillars into a composite, maps the composite to a rating,
applies the vetoes, and assembles the :class:`InvestmentDecision`. This is the
end of the deterministic core: past this point a language model may *explain*
the rating (Phase 4) but can never change it.

The honest handling of missing pillars is the crux. Three of the seven pillars
(industry, news, risk) need the Phase 3 agents; in a pure Phase 2 run they are
absent, and the valuation pillar is often only partly computable without a peer
set. Rather than score a missing pillar as zero — punishing a company for a gap
in *our* coverage — or silently pretend the remaining pillars are the whole
model, the composite is renormalised over the pillars actually scored, the
coverage is recorded explicitly, and the result is labelled ``QUANT_CORE``
rather than ``FULL``. A partial model that says so is honest; one that hides it
is a different model wearing the same name (PRD 6).
"""

from __future__ import annotations

from typing import Any

from app.config.settings import load_thresholds, load_weights
from app.engine.agent_scoring import news_pillar, score_industry, score_risk
from app.engine.guardrails import apply_vetoes, evaluate_vetoes
from app.engine.normalisation import apply_scores, is_bfsi, pillar_score
from app.engine.valuation import build_valuation, valuation_pillar_metrics
from app.models.agent_io import (
    IndustryAssessment,
    NewsAnalysis,
    RiskAssessment,
    ValuationAssumptions,
)
from app.models.decision import (
    Confidence,
    InvestmentDecision,
    PillarScore,
    Rating,
    RatingBasis,
    Valuation,
)
from app.models.metrics import MetricSet, MetricValue
from app.models.statements import FinancialStatements, MarketData

# Order the pillars are reported in.
PILLAR_ORDER = ("fundamentals", "valuation", "growth", "cashflow", "industry", "news", "risk")
# Pillars whose score enters the composite inverted (high raw score = more risk).
INVERTED_PILLARS = frozenset({"risk"})


def rating_for_score(score: float, weights: dict[str, Any] | None = None) -> Rating:
    """Map a 0-100 composite onto a rating band (PRD 12.5)."""
    weights = weights or load_weights()
    bands = weights.get("rating_bands") or {}
    for name, (low, high) in bands.items():
        if float(low) <= score < float(high):
            return Rating(name)
    # The bands are validated contiguous over [0, 101) at load, so this is only
    # reachable for a score outside [0, 100]; clamp to the nearest band.
    return Rating.BUY if score >= 100 else Rating.SELL


def _effective_pillar_score(name: str, score: float) -> float:
    return 100.0 - score if name in INVERTED_PILLARS else score


def composite_score(
    pillar_scores: dict[str, float | None],
    weights: dict[str, Any] | None = None,
) -> tuple[float | None, float, dict[str, float]]:
    """Renormalise the configured pillar weights over the pillars actually
    scored and return ``(composite, weight_covered, effective_weights)``.

    ``weight_covered`` is the fraction of the full 1.0 pillar weight that was
    scorable — the headline honesty figure for a partial run.
    """
    weights = weights or load_weights()
    configured: dict[str, float] = {k: float(v) for k, v in (weights.get("pillars") or {}).items()}

    available = {name: s for name, s in pillar_scores.items() if s is not None}
    covered = sum(configured.get(name, 0.0) for name in available)
    if covered <= 0:
        return (None, 0.0, {})

    effective_weights: dict[str, float] = {}
    composite = 0.0
    for name, score in available.items():
        w = configured.get(name, 0.0) / covered
        effective_weights[name] = w
        composite += w * _effective_pillar_score(name, score)
    return (composite, covered, effective_weights)


def _score_valuation_pillar(metric_set: MetricSet, valuation: Valuation) -> dict[str, MetricValue]:
    """Build and band-score the four valuation-pillar component metrics."""
    val_metrics = valuation_pillar_metrics(valuation)
    apply_scores(val_metrics, sector=metric_set.sector_key, thresholds=load_thresholds())
    return val_metrics


def _confidence(
    basis: RatingBasis, covered: float, dcf_low_confidence: bool, downgraded: bool
) -> Confidence:
    if basis in (RatingBasis.NO_RATING, RatingBasis.NOT_SUPPORTED):
        return Confidence.LOW
    if downgraded or dcf_low_confidence or covered < 0.60:
        return Confidence.LOW
    if basis is RatingBasis.FULL and covered >= 0.999:
        return Confidence.HIGH
    return Confidence.MEDIUM


def _data_completeness(metric_set: MetricSet, statements: FinancialStatements) -> float | None:
    """Prefer the data layer's completeness report; fall back to the share of
    the metric library that computed, as a deterministic proxy for fixtures
    that carry no report."""
    if statements.data_quality is not None:
        return statements.data_quality.completeness_pct
    available, total = metric_set.available_count()
    return None if total == 0 else available / total * 100.0


def build_decision(
    metric_set: MetricSet,
    statements: FinancialStatements,
    market: MarketData | None = None,
    *,
    peer_multiples: dict[str, list[float]] | None = None,
    own_history_multiples: dict[str, list[float]] | None = None,
    news: NewsAnalysis | None = None,
    industry: IndustryAssessment | None = None,
    risk: RiskAssessment | None = None,
    assumptions: ValuationAssumptions | None = None,
    weights: dict[str, Any] | None = None,
) -> InvestmentDecision:
    """Produce the deterministic decision for one company (PRD 12).

    ``metric_set`` is the Phase 1 output; the valuation pillar is computed here
    from ``statements``/``market`` (plus any peer/history inputs). The
    ``news``/``industry``/``risk`` agent outputs are optional: supplied (Phase
    3), they complete the seven-pillar composite and the basis becomes
    ``FULL``; absent (pure Phase 2), those pillars are missing and the basis is
    ``QUANT_CORE``. Whatever the agents supply is bounded structured data; the
    pillar scores are still computed here, in Python.
    """
    weights = weights or load_weights()
    stamp = {
        "weights_version": str(weights.get("version", "")),
        "thresholds_version": str(load_thresholds().get("version", "")),
    }
    from app.config.settings import load_valuation  # local: avoid import cost when unused

    valuation_version = str(load_valuation().get("version", ""))

    completeness = _data_completeness(metric_set, statements)

    # BFSI is out of scope in V1 (V8): an FCFF-DCF is invalid, so we do not even
    # attempt a valuation or a composite — we decline, clearly.
    if is_bfsi(statements.sector, statements.industry):
        vetoes = evaluate_vetoes(
            metric_set, statements, data_completeness_pct=completeness,
            upside_pct=None, valid_peer_count=0)
        return InvestmentDecision(
            ticker=metric_set.ticker, company_name=metric_set.company_name,
            period=metric_set.period, sector_key=metric_set.sector_key,
            composite_score=None, rating=Rating.NOT_SUPPORTED, basis=RatingBasis.NOT_SUPPORTED,
            confidence=Confidence.LOW, vetoes=vetoes, applied_vetoes=["V8"],
            data_completeness_pct=completeness,
            weights_version=stamp["weights_version"], thresholds_version=stamp["thresholds_version"],
            valuation_version=valuation_version,
            warnings=["BFSI sector: not supported in V1 (PRD 11.4). No rating issued."],
        )

    # Valuation and the valuation pillar.
    valuation = build_valuation(
        statements, market,
        peer_multiples=peer_multiples, own_history_multiples=own_history_multiples,
        assumptions=assumptions, weights=weights,
    )
    val_metrics = _score_valuation_pillar(metric_set, valuation)
    all_metrics: dict[str, MetricValue] = {**metric_set.metrics, **val_metrics}

    # Score every pillar. Fundamentals/valuation/growth/cashflow come from the
    # computed metrics; news/industry/risk come from the agent outputs when
    # supplied, each scored in Python. Un-computable pillars come back None and
    # are simply absent from the composite.
    pillar_scores: dict[str, float | None] = {}
    pillar_details: dict[str, tuple[dict[str, float], list[str]]] = {}
    for name in PILLAR_ORDER:
        if name == "news" and news is not None:
            score, contributions, notes = news_pillar(news, weights)
        elif name == "industry" and industry is not None:
            score, contributions, notes = score_industry(industry, weights)
        elif name == "risk" and risk is not None:
            score, contributions, notes = score_risk(risk, weights)
        else:
            score, contributions, notes = pillar_score(all_metrics, name, weights)
        pillar_scores[name] = score
        pillar_details[name] = (contributions, notes)

    composite, covered, effective_weights = composite_score(pillar_scores, weights)

    configured_weights = {k: float(v) for k, v in (weights.get("pillars") or {}).items()}
    scored = [n for n in PILLAR_ORDER if pillar_scores[n] is not None]
    missing = [n for n in PILLAR_ORDER if pillar_scores[n] is None]

    pillar_score_models: list[PillarScore] = []
    for name in PILLAR_ORDER:
        score = pillar_scores[name]
        eff_w = effective_weights.get(name)
        contribution = None
        if score is not None and eff_w is not None:
            contribution = round(eff_w * _effective_pillar_score(name, score), 2)
        contributions, notes = pillar_details[name]
        pillar_score_models.append(PillarScore(
            name=name, score=None if score is None else round(score, 2),
            configured_weight=configured_weights.get(name, 0.0),
            effective_weight=None if eff_w is None else round(eff_w, 4),
            contribution=contribution,
            components={k: round(v, 2) for k, v in contributions.items()},
            notes=notes,
        ))

    basis = RatingBasis.FULL if not missing else RatingBasis.QUANT_CORE

    vetoes = evaluate_vetoes(
        metric_set, statements,
        data_completeness_pct=completeness,
        upside_pct=valuation.upside_pct,
        valid_peer_count=(valuation.relative.valid_peer_count if valuation.relative else 0),
        governance_severity=(risk.governance_severity.value if risk is not None else None),
    )

    # Rating from the band, then vetoes.
    warnings: list[str] = list(metric_set.warnings)
    if composite is None:
        rating = Rating.NO_RATING
        basis = RatingBasis.NO_RATING
        applied: list[str] = []
        downgraded = False
        warnings.append("No pillar could be scored; no rating issued.")
    else:
        band_rating = rating_for_score(composite, weights)
        rating, applied, downgraded = apply_vetoes(band_rating, vetoes)
        if rating in (Rating.NO_RATING, Rating.NOT_SUPPORTED):
            basis = RatingBasis.NO_RATING if rating is Rating.NO_RATING else RatingBasis.NOT_SUPPORTED

    if basis is RatingBasis.QUANT_CORE:
        warnings.append(
            f"QUANT_CORE rating: {covered:.0%} of pillar weight scored deterministically; "
            f"pillars absent (need Phase 3 agents): {', '.join(missing)}. The composite is "
            f"renormalised over the pillars shown and is not the full seven-pillar score."
        )

    confidence = _confidence(basis, covered, valuation.dcf.confidence == "LOW_CONFIDENCE", downgraded)

    return InvestmentDecision(
        ticker=metric_set.ticker, company_name=metric_set.company_name,
        period=metric_set.period, sector_key=metric_set.sector_key,
        composite_score=None if composite is None else round(composite, 2),
        rating=rating, basis=basis, confidence=confidence,
        pillar_scores=pillar_score_models, pillars_scored=scored, pillars_missing=missing,
        weight_covered=round(covered, 4),
        vetoes=vetoes, applied_vetoes=applied,
        valuation=valuation, data_completeness_pct=None if completeness is None else round(completeness, 1),
        weights_version=stamp["weights_version"], thresholds_version=stamp["thresholds_version"],
        valuation_version=valuation_version, warnings=warnings,
    )
