"""Agent input/output contracts (PRD 8.2, 8.6–8.10).

These are the schemas the Phase 3 LLM agents read and write. Two rules shape
every one of them, and they are the whole reason the project can call its
recommendation *computed* rather than *generated* (PRD 6):

1. **The model emits bounded, structured judgements — never a score, never a
   rating.** An article's ``materiality`` is an integer 1-5; a sentiment is one
   of three enum values; an industry outlook is 1-5. There is deliberately no
   free-float "news_score" field the model can write: the score is computed in
   Python from these bounded fields (see ``app/engine/agent_scoring.py``).
2. **Every value the model can set is range-checked here.** A materiality of 7
   or a confidence of 1.4 is rejected at the schema boundary, so a
   malfunctioning or prompt-injected model cannot smuggle an out-of-range
   number into the arithmetic.

Because these models live in ``app/models`` they are shared contracts, imported
by both the agents that fill them and the engine that scores them — but they
import nothing from ``app/agents``, keeping the dependency arrow one-way
(enforced by ``tests/unit/test_layering.py``).
"""

from __future__ import annotations

from enum import Enum

from pydantic import BaseModel, Field


# ---------------------------------------------------------------- enums ------

class Relevance(str, Enum):
    HIGH = "HIGH"
    MEDIUM = "MEDIUM"
    LOW = "LOW"
    NONE = "NONE"


class Sentiment(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"


class FinancialImpact(str, Enum):
    POSITIVE = "POSITIVE"
    NEGATIVE = "NEGATIVE"
    NEUTRAL = "NEUTRAL"
    UNCLEAR = "UNCLEAR"

    @property
    def direction(self) -> int:
        """+1 / -1 / 0 — the sign the news pillar arithmetic uses (PRD 12.3).

        Direction comes from the *financial* impact, not the sentiment: a
        popular price cut is positive sentiment but negative for margins, and
        it is the margin that moves the investment case."""
        return {
            FinancialImpact.POSITIVE: 1,
            FinancialImpact.NEGATIVE: -1,
            FinancialImpact.NEUTRAL: 0,
            FinancialImpact.UNCLEAR: 0,
        }[self]


class TimeHorizon(str, Enum):
    IMMEDIATE = "IMMEDIATE"
    SHORT = "SHORT"
    MEDIUM = "MEDIUM"
    LONG = "LONG"


class EventCategory(str, Enum):
    ORDER_WIN = "order_win"
    RESULTS = "results"
    GUIDANCE = "guidance"
    MANAGEMENT_CHANGE = "management_change"
    REGULATORY = "regulatory"
    CAPEX = "capex"
    MA = "M&A"
    LITIGATION = "litigation"
    DIVIDEND = "dividend"
    RATING_ACTION = "rating_action"
    MACRO_SECTOR = "macro_sector"
    OTHER = "other"


class GovernanceSeverity(str, Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"


# ------------------------------------------------------- symbol resolution ---

class Citation(BaseModel):
    """A sourced claim from allowlisted web research (PRD 9.4).

    Records everything §9.4 requires — URL, source, credibility tier, dates, and
    the specific claim it supports — so a reader can trace any qualitative
    statement to an allowlisted source, and the verifier can strip anything that
    is uncited."""

    url: str
    source: str
    tier: int
    published_date: str | None = None
    access_date: str | None = None
    claim: str = ""


class CompanyIdentity(BaseModel):
    """Output of the Symbol Resolution Agent (PRD 8.2)."""

    ticker: str
    ticker_nse: str | None = None
    ticker_bse: str | None = None
    isin: str | None = None
    legal_name: str
    sector: str | None = None
    industry: str | None = None
    market_cap_bucket: str | None = None
    currency: str = "INR"
    fiscal_year_end: str = "03-31"
    candidate_peers: list[str] = Field(default_factory=list)


# --------------------------------------------------------------- news --------

class ArticleAssessment(BaseModel):
    """One news article assessed into bounded, scored fields (PRD 8.6).

    Everything the model decides is here and range-checked. Notably absent: any
    per-article score or weight — the weight is computed in Python from
    materiality, source tier, confidence and recency.
    """

    title: str
    url: str | None = None
    source_name: str | None = None
    published_date: str | None = None      # ISO date; age is computed from it

    relevance: Relevance
    source_tier: int = Field(ge=1, le=3)
    event_category: EventCategory = EventCategory.OTHER
    sentiment: Sentiment = Sentiment.NEUTRAL
    financial_impact: FinancialImpact = FinancialImpact.UNCLEAR
    impact_area: list[str] = Field(default_factory=list)
    materiality: int = Field(ge=1, le=5)
    time_horizon: TimeHorizon = TimeHorizon.MEDIUM
    confidence: float = Field(ge=0.0, le=1.0)
    reasoning: str = ""
    evidence_quote: str = ""

    @property
    def scored(self) -> bool:
        """Whether this article passes the relevance gate (PRD 8.6 step 1)."""
        return self.relevance in (Relevance.HIGH, Relevance.MEDIUM)


class NewsAggregate(BaseModel):
    """Python-computed news pillar plus the LLM's aggregate synthesis (PRD 8.6)."""

    news_score: float                       # 0-100, computed in Python
    articles_considered: int
    articles_scored: int
    total_weight: float
    insufficient_coverage: bool = False
    top_positive: list[str] = Field(default_factory=list)
    top_concerns: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    contradicts_fundamentals: bool = False
    notes: list[str] = Field(default_factory=list)


class NewsAnalysis(BaseModel):
    """Full output of the News Analysis Agent."""

    lookback_days: int = 60
    as_of: str | None = None
    articles: list[ArticleAssessment] = Field(default_factory=list)
    aggregate: NewsAggregate | None = None  # filled by app/engine/agent_scoring


# ------------------------------------------------------------ industry -------

class IndustryAssessment(BaseModel):
    """Output of the Industry & Macro Agent (PRD 8.7).

    The four drivers are bounded 1-5. Two point 'up is good' (growth outlook)
    and two point 'up is bad' (competitive intensity, regulatory risk,
    cyclicality); the direction is handled in scoring, not here, so the model
    only ever reports a level.
    """

    industry_growth_outlook: int = Field(ge=1, le=5)
    competitive_intensity: int = Field(ge=1, le=5)
    regulatory_risk: int = Field(ge=1, le=5)
    cyclicality: int = Field(ge=1, le=5)

    demand_drivers: list[str] = Field(default_factory=list)
    structural_headwinds: list[str] = Field(default_factory=list)
    macro_sensitivities: list[str] = Field(default_factory=list)
    regulatory_environment: str = ""
    citations: list[str] = Field(default_factory=list)

    industry_score: float | None = None     # 0-100, computed in Python
    notes: list[str] = Field(default_factory=list)


# --------------------------------------------------------------- risk --------

class RiskAssessment(BaseModel):
    """Output of the Risk Agent (PRD 8.10).

    Each dimension is a 0-100 risk *level* (higher = riskier). The pillar score
    is their weighted average; it enters the composite inverted (100 - risk),
    so that more risk lowers the recommendation."""

    financial_risk: int = Field(ge=0, le=100)
    business_risk: int = Field(ge=0, le=100)
    valuation_risk: int = Field(ge=0, le=100)
    governance_flags: int = Field(ge=0, le=100)
    governance_severity: GovernanceSeverity = GovernanceSeverity.NONE

    key_risks: list[str] = Field(default_factory=list)
    veto_triggers: list[str] = Field(default_factory=list)

    risk_score: float | None = None          # 0-100, computed in Python
    notes: list[str] = Field(default_factory=list)


# ---------------------------------------------------- valuation assumptions --

class PeerData(BaseModel):
    """One peer in the comparison set (PRD 8.8)."""

    ticker: str
    name: str | None = None
    accepted: bool = True
    reject_reason: str | None = None
    multiples: dict[str, float] = Field(default_factory=dict)   # pe, pb, ev_ebitda, ...


class PeerComparison(BaseModel):
    """Output of the Peer Comparison Agent — selection is deterministic, the
    percentiles are Python, the review is the LLM's (PRD 8.8)."""

    subject_ticker: str
    peers: list[PeerData] = Field(default_factory=list)
    peer_multiples: dict[str, list[float]] = Field(default_factory=dict)  # multiple -> accepted values
    valid_peer_count: int = 0
    notes: list[str] = Field(default_factory=list)


class ValuationAssumptions(BaseModel):
    """Growth/margin path proposed by the Valuation Agent (PRD 8.9).

    Consumed by ``app/engine/dcf.py`` as an override for its deterministic
    default. Bounds that the DCF guardrails also enforce are declared here so a
    breach is caught at the boundary."""

    revenue_growth_path_pct: list[float]
    ebit_margin: float | None = None         # held flat if None -> engine uses latest
    # Permissive schema bound; the agent's guardrail clamps to the tight
    # 3.0-5.5% band (PRD 8.9). The wide bound lets an out-of-band proposal
    # through to be *corrected and recorded* rather than rejected unseen.
    terminal_growth_pct: float = Field(ge=0.0, le=15.0)
    rationale: str = ""
    guardrail_notes: list[str] = Field(default_factory=list)
