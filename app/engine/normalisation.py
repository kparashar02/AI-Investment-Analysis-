"""Metric normalisation: raw value to 0-100 score.

This module is where a financial fact becomes a judgement, and it is the
narrowest place in the system where that transition happens. Everything
upstream is arithmetic; everything downstream is weighting. Keeping the
value-laden step small, declarative and in one file is what makes the
scoring model arguable rather than mysterious.

The curves live in ``thresholds.yaml``. No threshold is hard-coded here.
"""

from __future__ import annotations

from typing import Any

from app.config.settings import load_thresholds, load_weights
from app.engine.helpers import interpolate, percentile_rank
from app.models.metrics import MetricValue

# Free-text sector strings from data providers are mapped onto the sector keys
# used by thresholds.yaml. Ordered: first substring match wins, so the more
# specific patterns must come first.
SECTOR_PATTERNS: list[tuple[tuple[str, ...], str]] = [
    (("information technology", "it services", "software", "technology", "infotech"), "it_services"),
    (("pharmaceutical", "pharma", "drug", "life science", "healthcare", "biotech"), "pharmaceuticals"),
    (("fmcg", "consumer staple", "personal product", "household", "food", "beverage", "tobacco"), "fmcg"),
    (("capital good", "industrial", "engineering", "machinery", "construction", "infrastructure", "defence"), "capital_goods"),
    (("metal", "mining", "steel", "aluminium", "aluminum", "cement", "commodity"), "metals_mining"),
    (("utility", "utilities", "power", "electric", "gas distribution", "renewable"), "utilities"),
]

# Sectors excluded from V1 by veto rule V8: an FCFF-based DCF is invalid for a
# business whose debt is raw material rather than financing, and the metric
# set that matters for them (NIM, GNPA, CASA, CAR, provision coverage) is not
# implemented. Detected here so the system refuses to rate rather than
# producing a confidently invalid valuation.
BFSI_PATTERNS: tuple[str, ...] = (
    "bank", "financial service", "finance", "nbfc", "insurance", "insurer",
    "asset management", "capital market", "broking", "housing finance",
    "lending", "microfinance",
)


def sector_key(sector: str | None, industry: str | None = None) -> str:
    """Map a provider's free-text sector onto a thresholds.yaml sector key."""
    haystack = " ".join(part.lower() for part in (sector, industry) if part)
    if not haystack:
        return "general"
    for patterns, key in SECTOR_PATTERNS:
        if any(pattern in haystack for pattern in patterns):
            return key
    return "general"


def is_bfsi(sector: str | None, industry: str | None = None) -> bool:
    """Whether this company falls in the sector V1 declines to rate."""
    haystack = " ".join(part.lower() for part in (sector, industry) if part)
    return any(pattern in haystack for pattern in BFSI_PATTERNS)


def _spec_for(metric_name: str, sector: str, thresholds: dict[str, Any]) -> dict | None:
    """Metric spec with any sector override merged over the base."""
    base = (thresholds.get("metrics") or {}).get(metric_name)
    if base is None:
        return None
    override = ((thresholds.get("sector_overrides") or {}).get(sector) or {}).get(metric_name)
    if not override:
        return base
    return {**base, **override}


def band_score(
    metric_name: str,
    value: float | None,
    sector: str = "general",
    thresholds: dict[str, Any] | None = None,
) -> tuple[float | None, str | None]:
    """Score a raw metric value against its curve.

    Returns ``(score, note)``. ``note`` records a sector override having been
    applied, so the report can say which standard the company was judged
    against.
    """
    if value is None:
        return (None, None)
    thresholds = thresholds or load_thresholds()
    spec = _spec_for(metric_name, sector, thresholds)
    if spec is None:
        return (None, None)     # metric is computed but deliberately not scored

    score = interpolate(spec["curve"], value)
    overridden = bool(
        ((thresholds.get("sector_overrides") or {}).get(sector) or {}).get(metric_name)
    )
    note = f"Scored against the '{sector}' sector curve." if overridden else None
    return (score, note)


def blend_scores(
    band: float | None,
    percentile: float | None,
    thresholds: dict[str, Any] | None = None,
) -> float | None:
    """Combine the absolute band score with peer standing.

    A metric is judged both against an absolute financial standard and
    against what is actually achievable in its industry: a 12% ROE is
    mediocre in IT services and strong in a regulated utility. With no peer
    set — which is every Phase 1 run — the band score stands alone rather
    than being diluted against nothing.
    """
    if band is None:
        return None
    if percentile is None:
        return band
    thresholds = thresholds or load_thresholds()
    blend = thresholds.get("blend") or {}
    w_band = float(blend.get("absolute_band", 0.6))
    w_pct = float(blend.get("peer_percentile", 0.4))
    return w_band * band + w_pct * percentile


def apply_scores(
    metrics: dict[str, MetricValue],
    sector: str = "general",
    peer_values: dict[str, list[float | None]] | None = None,
    thresholds: dict[str, Any] | None = None,
) -> dict[str, MetricValue]:
    """Populate ``band_score``, ``peer_percentile`` and ``final_score`` in place.

    ``peer_values`` maps a metric name to the values for the peer set
    *including this company's own value*. Absent in Phase 1; supplied by the
    Peer Comparison Agent in Phase 3.
    """
    thresholds = thresholds or load_thresholds()
    for name, metric in metrics.items():
        score, note = band_score(name, metric.value, sector, thresholds)
        metric.band_score = score
        if note and note not in metric.notes:
            metric.notes.append(note)

        if peer_values and name in peer_values:
            metric.peer_percentile = percentile_rank(metric.value, peer_values[name])

        metric.final_score = blend_scores(metric.band_score, metric.peer_percentile, thresholds)
    return metrics


def pillar_score(
    metrics: dict[str, MetricValue],
    pillar: str,
    weights: dict[str, Any] | None = None,
) -> tuple[float | None, dict[str, float], list[str]]:
    """Weighted score for one pillar, redistributing over missing components.

    Returns ``(score, contributions, notes)``.

    When a component metric is unavailable its weight is spread
    proportionally across the components that *are* available, and the
    redistribution is reported in ``notes``. The alternative — scoring a
    missing metric as zero — would punish a company for a gap in its data
    provider's coverage, which is a data problem masquerading as a
    fundamental one.

    If more than half a pillar's weight is missing, the pillar returns
    ``None``: at that point there is not enough left to call it a measurement.
    """
    weights = weights or load_weights()
    components: dict[str, float] = (weights.get("components") or {}).get(pillar) or {}
    if not components:
        return (None, {}, [f"no components configured for pillar '{pillar}'"])

    available: dict[str, float] = {}
    missing: list[str] = []
    for name, weight in components.items():
        metric = metrics.get(name)
        if metric is not None and metric.final_score is not None:
            available[name] = float(weight)
        else:
            missing.append(name)

    notes: list[str] = []
    available_weight = sum(available.values())
    if available_weight <= 0:
        return (None, {}, [f"no component of pillar '{pillar}' could be computed"])
    if available_weight < 0.5:
        return (
            None, {},
            [
                f"only {available_weight:.0%} of pillar '{pillar}' weight is "
                f"computable (missing: {', '.join(sorted(missing))}); pillar not scored"
            ],
        )
    if missing:
        notes.append(
            f"{', '.join(sorted(missing))} unavailable; "
            f"{1 - available_weight:.0%} of pillar weight redistributed proportionally."
        )

    contributions: dict[str, float] = {}
    total = 0.0
    for name, weight in available.items():
        normalised = weight / available_weight
        score = metrics[name].final_score
        assert score is not None            # guarded above
        contribution = normalised * score
        contributions[name] = contribution
        total += contribution

    return (total, contributions, notes)
