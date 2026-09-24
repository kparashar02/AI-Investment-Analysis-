"""Decision-layer models: valuation, vetoes, and the investment decision.

These are the Phase 2 output contracts. The central one, :class:`InvestmentDecision`,
is produced entirely by deterministic Python (``app/engine/scoring.py``,
``guardrails.py``, ``valuation.py``) and is deliberately built so that **no
field on it can be written by a language model**. The narrative agent (Phase 4)
receives a fully-formed decision as a fixed fact to explain; it has nowhere to
write a different rating, and ``tests/unit/test_layering.py`` asserts that this
object carries no free-text field it could hide one in. That schema-level
constraint is the separation of concerns (PRD 6) made structural rather than
merely promised.
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


class Rating(str, Enum):
    """The recommendation. Ordered best-to-worst by :attr:`rank` so a veto can
    *cap* a rating — force it no better than some level — deterministically."""

    BUY = "BUY"
    ACCUMULATE = "ACCUMULATE"
    HOLD = "HOLD"
    REDUCE = "REDUCE"
    SELL = "SELL"
    NO_RATING = "NO_RATING"
    NOT_SUPPORTED = "NOT_SUPPORTED"

    @property
    def rank(self) -> int:
        """0 = best (BUY). The non-ratings sort last and are never produced by
        a band lookup — only by a veto — so their exact order is immaterial."""
        return {
            Rating.BUY: 0,
            Rating.ACCUMULATE: 1,
            Rating.HOLD: 2,
            Rating.REDUCE: 3,
            Rating.SELL: 4,
            Rating.NO_RATING: 5,
            Rating.NOT_SUPPORTED: 6,
        }[self]

    def capped_at(self, ceiling: Rating) -> Rating:
        """This rating, but no better than ``ceiling`` (a CAP_AT_* veto action)."""
        return self if self.rank >= ceiling.rank else ceiling


class RatingBasis(str, Enum):
    """What the rating actually rests on.

    ``FULL`` — all seven pillars scored. ``QUANT_CORE`` — the deterministic
    subset available without the Phase 3 agents (fundamentals, valuation,
    growth, cash flow), renormalised and disclosed. Labelling this honestly is
    the difference between a partial model and a different model wearing the
    same name (PRD 6; README design principle).
    """

    FULL = "FULL"
    QUANT_CORE = "QUANT_CORE"
    NO_RATING = "NO_RATING"
    NOT_SUPPORTED = "NOT_SUPPORTED"


class Confidence(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"


class SensitivityGrid(BaseModel):
    """Fair value per share across WACC (rows) × terminal growth (columns).

    The DCF is presented as this grid, never as a single number: a point
    estimate dressed as precision is a methodological error (PRD 11.1)."""

    wacc_values_pct: list[float]
    growth_values_pct: list[float]
    grid: list[list[float | None]]  # grid[i][j] = fair value at wacc_values[i], growth_values[j]
    base_wacc_pct: float
    base_growth_pct: float


class DCFResult(BaseModel):
    """Two-stage FCFF DCF output (PRD 11.1)."""

    available: bool = True
    unavailable_reason: str | None = None
    confidence: str = "BASE"          # BASE | LOW_CONFIDENCE
    flags: list[str] = Field(default_factory=list)

    # WACC build-up
    cost_of_equity_pct: float | None = None
    cost_of_debt_pct: float | None = None
    beta_used: float | None = None
    tax_rate: float | None = None
    wacc_pct: float | None = None
    equity_weight: float | None = None
    debt_weight: float | None = None

    # Forecast
    revenue_growth_path_pct: list[float] = Field(default_factory=list)
    fcff_forecast: list[float] = Field(default_factory=list)
    terminal_growth_pct: float | None = None
    terminal_value: float | None = None

    # Values (crore, then per share)
    enterprise_value: float | None = None
    net_debt: float | None = None
    equity_value: float | None = None
    fair_value_base: float | None = None
    fair_value_low: float | None = None
    fair_value_high: float | None = None

    sensitivity: SensitivityGrid | None = None
    assumptions: dict[str, float] = Field(default_factory=dict)


class MultipleComparison(BaseModel):
    """One multiple compared to a benchmark (peers or own history)."""

    multiple: str                      # 'pe', 'ev_ebitda', ...
    company_value: float | None = None
    benchmark_value: float | None = None
    premium_discount_pct: float | None = None   # (company/benchmark - 1) * 100
    implied_value_per_share: float | None = None
    peer_percentile: float | None = None


class RelativeValuationResult(BaseModel):
    """Peer-relative and own-history valuation (PRD 11.2)."""

    available: bool = False
    valid_peer_count: int = 0
    peer_comparisons: list[MultipleComparison] = Field(default_factory=list)
    own_history_comparisons: list[MultipleComparison] = Field(default_factory=list)
    implied_low: float | None = None      # per share, blended across multiples
    implied_high: float | None = None
    quality_percentile: float | None = None
    valuation_percentile: float | None = None
    premium_justified: bool | None = None
    notes: list[str] = Field(default_factory=list)


class ValuationMethod(BaseModel):
    """One method's contribution to the reconciled range (PRD 11.3)."""

    method: str                        # 'dcf' | 'peer_relative' | 'own_history'
    low: float | None = None
    high: float | None = None
    weight: float                      # effective weight after any LOW_CONFIDENCE halving
    included: bool = True
    note: str | None = None


class Valuation(BaseModel):
    """The reconciled fair value and everything behind it (PRD 11)."""

    current_price: float | None = None
    dcf: DCFResult
    relative: RelativeValuationResult | None = None

    methods: list[ValuationMethod] = Field(default_factory=list)
    reconciled_low: float | None = None
    reconciled_high: float | None = None
    fair_value_midpoint: float | None = None
    upside_pct: float | None = None       # (midpoint / price - 1) * 100
    notes: list[str] = Field(default_factory=list)


class VetoTrigger(BaseModel):
    """One guardrail rule and how it evaluated (PRD 12.6)."""

    code: str                          # 'V1' .. 'V10'
    description: str
    action: str                        # NO_RATING | CAP_AT_REDUCE | ...
    evaluable: bool                    # False when it needs a Phase 3/4 input we lack
    triggered: bool
    values: dict[str, float | str | bool] = Field(default_factory=dict)
    note: str | None = None


class PillarScore(BaseModel):
    """One pillar's score and its contribution to the composite."""

    name: str
    score: float | None                # 0-100, or None if not computable
    configured_weight: float           # its weight in the full 7-pillar model
    effective_weight: float | None = None   # after renormalisation over available pillars
    contribution: float | None = None       # effective_weight * score
    components: dict[str, float] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)


class InvestmentDecision(BaseModel):
    """The complete deterministic decision for one company.

    Carries the composite score, the rating, the valuation, every pillar's
    contribution and every veto's evaluation — the full audit trail a reader
    needs to argue with the *number* rather than the black box. It holds no
    field a narrative agent could use to alter the recommendation.
    """

    ticker: str
    company_name: str
    period: str
    sector_key: str = "general"

    composite_score: float | None = None
    rating: Rating
    basis: RatingBasis
    confidence: Confidence = Confidence.MEDIUM

    pillar_scores: list[PillarScore] = Field(default_factory=list)
    pillars_scored: list[str] = Field(default_factory=list)
    pillars_missing: list[str] = Field(default_factory=list)
    weight_covered: float = 0.0        # fraction of total pillar weight actually scored

    vetoes: list[VetoTrigger] = Field(default_factory=list)
    applied_vetoes: list[str] = Field(default_factory=list)

    valuation: Valuation | None = None
    data_completeness_pct: float | None = None

    weights_version: str = ""
    thresholds_version: str = ""
    valuation_version: str = ""
    warnings: list[str] = Field(default_factory=list)

    @property
    def rated(self) -> bool:
        return self.rating not in (Rating.NO_RATING, Rating.NOT_SUPPORTED)

    def fingerprint(self) -> str:
        """Stable hash of the decision's load-bearing numbers, for the
        determinism test (PRD 12.7): identical inputs must hash identically."""
        import hashlib

        parts = [
            f"composite={self.composite_score!r}",
            f"rating={self.rating.value}",
            f"basis={self.basis.value}",
        ]
        for pillar in sorted(self.pillar_scores, key=lambda p: p.name):
            parts.append(f"{pillar.name}={pillar.score!r}:{pillar.contribution!r}")
        for code in sorted(self.applied_vetoes):
            parts.append(f"veto={code}")
        if self.valuation is not None:
            parts.append(f"fv={self.valuation.fair_value_midpoint!r}")
            parts.append(f"upside={self.valuation.upside_pct!r}")
        return hashlib.sha256("|".join(parts).encode()).hexdigest()[:16]
