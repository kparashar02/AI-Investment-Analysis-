"""Valuation reconciliation and the valuation pillar (PRD 11.3, 12.3).

Ties the DCF and relative valuation into one reconciled fair-value range, then
expresses that range as the four scored components of the valuation pillar
(``upside_to_fair_value`` and the three multiple comparisons in weights.yaml).
Those come back as ordinary :class:`MetricValue` objects so the existing
normalisation and pillar-scoring machinery scores them exactly like every other
metric — the valuation pillar is not a special case in the scorer.

Reconciliation weights live in ``weights.yaml``; a LOW_CONFIDENCE DCF has its
weight halved and the remainder redistributed proportionally (PRD 11.3).
"""

from __future__ import annotations

from typing import Any

from app.config.settings import load_valuation, load_weights
from app.engine.dcf import compute_dcf, default_assumptions
from app.engine.relative_valuation import compute_relative_valuation
from app.models.agent_io import ValuationAssumptions
from app.models.decision import (
    DCFResult,
    RelativeValuationResult,
    Valuation,
    ValuationMethod,
)
from app.models.metrics import MetricValue, Pillar, Unit
from app.models.statements import FinancialStatements, MarketData

_DEFAULT_METHOD_WEIGHTS = {"dcf": 0.50, "peer_relative": 0.30, "own_history": 0.20}


def _method_ranges(
    dcf: DCFResult, relative: RelativeValuationResult
) -> dict[str, tuple[float, float]]:
    """Each available method's (low, high) per-share range."""
    ranges: dict[str, tuple[float, float]] = {}
    if dcf.available and dcf.fair_value_low is not None and dcf.fair_value_high is not None:
        ranges["dcf"] = (dcf.fair_value_low, dcf.fair_value_high)

    peer_implied = [c.implied_value_per_share for c in relative.peer_comparisons
                    if c.implied_value_per_share is not None]
    if peer_implied:
        ranges["peer_relative"] = (min(peer_implied), max(peer_implied))

    own_implied = [c.implied_value_per_share for c in relative.own_history_comparisons
                   if c.implied_value_per_share is not None]
    if own_implied:
        ranges["own_history"] = (min(own_implied), max(own_implied))
    return ranges


def _reconcile(
    ranges: dict[str, tuple[float, float]],
    dcf_low_confidence: bool,
    method_weights: dict[str, float],
    low_conf_multiplier: float,
) -> tuple[list[ValuationMethod], float | None, float | None]:
    """Weight-average the available method ranges (PRD 11.3)."""
    effective: dict[str, float] = {}
    for name in ranges:
        w = method_weights.get(name, _DEFAULT_METHOD_WEIGHTS.get(name, 0.0))
        if name == "dcf" and dcf_low_confidence:
            w *= low_conf_multiplier
        effective[name] = w

    total = sum(effective.values())
    methods: list[ValuationMethod] = []
    recon_low = recon_high = None
    if total > 0:
        recon_low = 0.0
        recon_high = 0.0
        for name, (low, high) in ranges.items():
            w = effective[name] / total
            recon_low += w * low
            recon_high += w * high
            note = None
            if name == "dcf" and dcf_low_confidence:
                note = "DCF weight halved (LOW_CONFIDENCE)"
            methods.append(ValuationMethod(method=name, low=round(low, 2), high=round(high, 2),
                                           weight=round(w, 4), included=True, note=note))
    # Record excluded methods too, for transparency.
    for name in _DEFAULT_METHOD_WEIGHTS:
        if name not in ranges:
            methods.append(ValuationMethod(method=name, weight=0.0, included=False,
                                           note="no inputs available"))
    return methods, (round(recon_low, 2) if recon_low is not None else None), \
        (round(recon_high, 2) if recon_high is not None else None)


def _dcf_assumptions_from_agent(
    statements: FinancialStatements, cfg: dict[str, Any], agent: ValuationAssumptions,
) -> dict[str, Any] | None:
    """Merge the agent's growth-fade path onto the engine's mechanical defaults.

    Only the revenue growth path is taken from the model — the assumption the
    PRD explicitly assigns to it (8.9). Margin and intensity stay on their
    history-derived defaults, and terminal growth stays under the engine's WACC
    guardrail, so the LLM cannot destabilise the terminal value."""
    path = list(agent.revenue_growth_path_pct)
    if not path:
        return None
    horizon = int((cfg.get("forecast") or {}).get("horizon_years", 5))
    g_max = float((cfg.get("terminal_growth") or {}).get("max_pct", 5.5))
    base = default_assumptions(statements, cfg, g_max)  # placeholder g; path is replaced below
    if len(path) < horizon:
        path = path + [path[-1]] * (horizon - len(path))
    elif len(path) > horizon:
        path = path[:horizon]
    base["revenue_growth_path_pct"] = path
    base["source"] = "valuation_agent"
    return base


def build_valuation(
    statements: FinancialStatements,
    market: MarketData | None,
    *,
    peer_multiples: dict[str, list[float]] | None = None,
    own_history_multiples: dict[str, list[float]] | None = None,
    quality_percentile: float | None = None,
    assumptions: ValuationAssumptions | None = None,
    valuation_config: dict[str, Any] | None = None,
    weights: dict[str, Any] | None = None,
) -> Valuation:
    """Compute the DCF, relative valuation, and their reconciliation.

    ``assumptions`` (from the valuation agent) overrides only the DCF's revenue
    growth path; absent, the DCF uses its deterministic default."""
    cfg = valuation_config or load_valuation()
    weights = weights or load_weights()
    method_weights = {**_DEFAULT_METHOD_WEIGHTS, **(weights.get("valuation_methods") or {})}
    low_conf_mult = float((cfg.get("reconciliation") or {}).get("low_confidence_dcf_weight_multiplier", 0.5))

    dcf_assumptions = _dcf_assumptions_from_agent(statements, cfg, assumptions) if assumptions else None
    dcf = compute_dcf(statements, market, cfg, assumptions=dcf_assumptions)
    relative = compute_relative_valuation(
        statements, market,
        peer_multiples=peer_multiples,
        own_history_multiples=own_history_multiples,
        quality_percentile=quality_percentile,
        config=cfg,
    )

    ranges = _method_ranges(dcf, relative)
    methods, recon_low, recon_high = _reconcile(
        ranges, dcf.confidence == "LOW_CONFIDENCE", method_weights, low_conf_mult
    )

    price = market.cmp if market is not None else None
    midpoint = upside = None
    if recon_low is not None and recon_high is not None:
        midpoint = round((recon_low + recon_high) / 2.0, 2)
        if price:
            upside = round((midpoint / price - 1.0) * 100.0, 2)

    notes: list[str] = []
    if not ranges:
        notes.append("No valuation method could be computed; the valuation pillar is unavailable.")
    elif "dcf" in ranges and len(ranges) == 1:
        notes.append("Reconciled fair value rests on the DCF alone (no peers, no multiple history).")

    return Valuation(
        current_price=price,
        dcf=dcf,
        relative=relative,
        methods=methods,
        reconciled_low=recon_low,
        reconciled_high=recon_high,
        fair_value_midpoint=midpoint,
        upside_pct=upside,
        notes=notes,
    )


def valuation_pillar_metrics(valuation: Valuation) -> dict[str, MetricValue]:
    """The four scored components of the valuation pillar (weights.yaml).

    Each is an ordinary MetricValue with its ``value`` set; the caller runs the
    standard ``apply_scores`` over them so they are banded like any other
    metric. Components whose inputs are absent are emitted as unavailable, so
    the pillar redistributes their weight rather than scoring a guess.
    """
    period = None
    out: dict[str, MetricValue] = {}

    def unavailable(name: str, label: str, reason: str) -> MetricValue:
        return MetricValue(name=name, label=label, value=None, unit=Unit.PCT,
                           pillar=Pillar.VALUATION, unavailable_reason=reason)

    # 1. Upside to reconciled fair value.
    if valuation.upside_pct is not None:
        out["upside_to_fair_value"] = MetricValue(
            name="upside_to_fair_value", label="Upside to Reconciled Fair Value",
            value=valuation.upside_pct, unit=Unit.PCT, pillar=Pillar.VALUATION, period=period,
            formula="(reconciled fair value midpoint / CMP - 1)",
            inputs_used={"fair_value_midpoint": valuation.fair_value_midpoint or 0.0,
                         "cmp": valuation.current_price or 0.0},
        )
    else:
        out["upside_to_fair_value"] = unavailable(
            "upside_to_fair_value", "Upside to Reconciled Fair Value",
            "no reconciled fair value (valuation methods unavailable)")

    rel = valuation.relative

    def multiple_premium(name: str, label: str, source: str, key: str) -> MetricValue:
        comparisons = getattr(rel, source) if rel else []
        for comp in comparisons:
            if comp.multiple == key and comp.premium_discount_pct is not None:
                return MetricValue(
                    name=name, label=label, value=comp.premium_discount_pct, unit=Unit.PCT,
                    pillar=Pillar.VALUATION, period=period,
                    formula=f"({key} / benchmark - 1)",
                    inputs_used={"company": comp.company_value or 0.0,
                                 "benchmark": comp.benchmark_value or 0.0},
                )
        return unavailable(name, label, "no peer/history multiple available")

    out["pe_vs_peer_median"] = multiple_premium(
        "pe_vs_peer_median", "P/E vs. Peer Median", "peer_comparisons", "pe")
    out["ev_ebitda_vs_peer_median"] = multiple_premium(
        "ev_ebitda_vs_peer_median", "EV/EBITDA vs. Peer Median", "peer_comparisons", "ev_ebitda")
    out["pe_vs_own_history"] = multiple_premium(
        "pe_vs_own_history", "P/E vs. Own 5-Year Median", "own_history_comparisons", "pe")
    return out
