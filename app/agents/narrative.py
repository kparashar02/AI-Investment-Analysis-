"""Investment Analyst (Narrative) Agent (PRD 8.12).

Writes the report that *explains* the already-fixed rating. The rating, the
composite score and every valuation number are passed to it as facts to be
explained, not questions to be answered — and there is no field on the output
it could use to change them (they live on the :class:`InvestmentDecision`, which
this agent only reads). Its hard constraints (PRD 8.12): it cannot change the
rating; it must not state a number absent from the state (enforced by the
verifier, not trusted); it must surface disagreement between pillars; it must
include a "what would change our view" section; and if it believes the score is
wrong it says so in ``analyst_caveat`` rather than altering the recommendation
(PRD 20.3 E8).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import Agent
from app.models.agent_io import IndustryAssessment, NewsAnalysis
from app.models.decision import InvestmentDecision
from app.models.report import (
    InvestmentView,
    ReportSection,
    ResearchReport,
    WhatWouldChangeView,
    build_disclaimer,
)

_SYSTEM = (
    "You are an equity research analyst WRITING UP a decision that has ALREADY "
    "been made by a deterministic scoring engine. The rating and score are fixed "
    "facts you must explain, not change. Rules: (1) never contradict or restate a "
    "different rating; (2) do not state any number that is not given to you in "
    "the facts — round, don't invent; (3) surface disagreement between pillars "
    "rather than smoothing it over; (4) state the bear case fairly; (5) give "
    "specific, monitorable upgrade/downgrade triggers. If you genuinely believe "
    "the score misreads the company, put that ONLY in analyst_caveat — never in "
    "the recommendation. Write concise, professional prose."
)


class _NarrativeDraft(BaseModel):
    """Exactly what the model writes — prose, never a number it chose as a score."""

    executive_summary: list[str] = Field(default_factory=list)
    rating_rationale: str = ""
    financial_commentary: str = ""
    growth_commentary: str = ""
    cashflow_commentary: str = ""
    valuation_commentary: str = ""
    industry_commentary: str = ""
    news_commentary: str = ""
    risk_commentary: str = ""
    supports_rating: list[str] = Field(default_factory=list)
    argues_against: list[str] = Field(default_factory=list)
    bear_case: str = ""
    upgrade_triggers: list[str] = Field(default_factory=list)
    downgrade_triggers: list[str] = Field(default_factory=list)
    monitorable_metrics: list[str] = Field(default_factory=list)
    analyst_caveat: str | None = None


def _facts(decision: InvestmentDecision, extra: str) -> str:
    """The compact, authoritative fact sheet handed to the model."""
    lines = [
        f"Company: {decision.company_name} [{decision.ticker}]  Period: {decision.period}",
        f"RATING (FIXED): {decision.rating.value}   Basis: {decision.basis.value}",
        f"Composite score (FIXED): {decision.composite_score}",
        f"Confidence: {decision.confidence.value}",
    ]
    for pillar in decision.pillar_scores:
        if pillar.score is not None:
            lines.append(f"  pillar {pillar.name}: {pillar.score}")
    val = decision.valuation
    if val is not None and val.upside_pct is not None:
        lines.append(f"CMP: {val.current_price}  Fair value: {val.reconciled_low}-{val.reconciled_high}  "
                     f"Upside: {val.upside_pct}%")
    fired = [v.code for v in decision.vetoes if v.triggered]
    if fired:
        lines.append(f"Vetoes triggered: {', '.join(fired)}")
    if extra:
        lines.append(extra)
    return "\n".join(lines)


class NarrativeAgent(Agent):
    name = "narrative"
    model_tier = "opus"

    def run(
        self,
        decision: InvestmentDecision,
        *,
        industry: IndustryAssessment | None = None,
        news: NewsAnalysis | None = None,
        extra_facts: str = "",
        as_of: str | None = None,
        generated_at: str | None = None,
        feedback: str | None = None,
    ) -> ResearchReport:
        """Write the report for ``decision``. ``feedback`` lets the verifier ask
        for a regeneration citing the specific problems it found."""
        user = f"FACTS (authoritative):\n{_facts(decision, extra_facts)}\n\nWrite the report."
        if feedback:
            user += (f"\n\nYOUR PREVIOUS DRAFT FAILED VERIFICATION. Fix these and rewrite, "
                     f"stating only numbers present in the facts:\n{feedback}")
        draft: _NarrativeDraft = self._ask(_SYSTEM, user, _NarrativeDraft)  # type: ignore[assignment]
        return self._assemble(draft, decision, industry, news, as_of, generated_at)

    def _assemble(
        self, draft: _NarrativeDraft, decision: InvestmentDecision,
        industry: IndustryAssessment | None, news: NewsAnalysis | None,
        as_of: str | None, generated_at: str | None,
    ) -> ResearchReport:
        sections = [
            ReportSection(number=4, title="Financial Performance", body=draft.financial_commentary),
            ReportSection(number=6, title="Cash Flow & Earnings Quality", body=draft.cashflow_commentary),
            ReportSection(number=7, title="Growth Analysis", body=draft.growth_commentary),
            ReportSection(number=9, title="Valuation", body=draft.valuation_commentary),
            ReportSection(number=10, title="Industry & Macro Context", body=draft.industry_commentary),
            ReportSection(number=11, title="News & Event Analysis", body=draft.news_commentary),
            ReportSection(number=12, title="Risk Assessment", body=draft.risk_commentary),
        ]
        citations: list[str] = []
        if industry is not None:
            citations.extend(industry.citations)
        if news is not None:
            citations.extend(a.url for a in news.articles if a.url)

        return ResearchReport(
            ticker=decision.ticker, company_name=decision.company_name,
            as_of=as_of, generated_at=generated_at,
            executive_summary=draft.executive_summary,
            rating_rationale=draft.rating_rationale,
            investment_view=InvestmentView(
                supports_rating=draft.supports_rating,
                argues_against=draft.argues_against,
                bear_case=draft.bear_case),
            what_would_change_our_view=WhatWouldChangeView(
                upgrade_triggers=draft.upgrade_triggers,
                downgrade_triggers=draft.downgrade_triggers,
                monitorable_metrics=draft.monitorable_metrics),
            analyst_caveat=draft.analyst_caveat,
            sections=sections,
            citations=citations,
            decision=decision,
            disclaimer=build_disclaimer(decision, data_as_of=as_of, generated_at=generated_at),
        )
