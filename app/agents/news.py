"""News Analysis Agent — the differentiating agent (PRD 8.6).

Each article is assessed **individually** into a bounded, scored
:class:`ArticleAssessment` (relevance gate, source tier, event category,
directional split of sentiment vs. financial impact, materiality 1-5, horizon,
confidence, an evidence quote). The per-article scores are then combined into
the news pillar **in Python** (``app/engine/agent_scoring.score_news``) — the
LLM never picks the number. A second, cheaper synthesis pass produces the
qualitative aggregate (top developments, concerns, themes, and any conflict
with the fundamentals).

The agent takes already-fetched raw articles; the fetch (Alpha Vantage / NewsAPI
/ RSS) is a separate provider so this stays testable with a fixed article set.
Article metadata (title, url, source, date) is authoritative from the raw feed
and overwrites whatever the model echoes back — the model judges, it does not
get to rewrite the facts.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import Agent
from app.agents.llm import LLMError
from app.engine.agent_scoring import score_news
from app.models.agent_io import ArticleAssessment, NewsAnalysis

_ARTICLE_SYSTEM = (
    "You are an equity analyst assessing ONE news item about a specific company. "
    "Work the pipeline: (1) relevance — is this actually about THIS company or a "
    "passing mention? HIGH/MEDIUM/LOW/NONE. (2) source_tier — 1 for exchange "
    "filings, company PR, Reuters, Bloomberg, Mint, Business Standard, Economic "
    "Times, Moneycontrol; 2 for mainstream/trade press; 3 for aggregators/blogs. "
    "(3) event_category. (4) sentiment vs financial_impact — these can diverge "
    "(a popular price cut is positive sentiment, negative for margins). "
    "(5) impact_area. (6) materiality 1-5 (5 = alters the investment thesis), "
    "time_horizon, confidence 0-1, and a verbatim evidence_quote of at most 25 "
    "words. Judge only; do not compute any score."
)

_SYNTHESIS_SYSTEM = (
    "You are synthesising a set of already-assessed news items about a company "
    "into a brief aggregate. Identify the top positive developments, the top "
    "concerns, recurring themes, and whether the news picture conflicts with the "
    "company's reported fundamentals. Do not output any numeric score."
)


class _NewsSynthesis(BaseModel):
    """Qualitative aggregate the model fills; the number is added in Python."""

    top_positive: list[str] = Field(default_factory=list)
    top_concerns: list[str] = Field(default_factory=list)
    themes: list[str] = Field(default_factory=list)
    contradicts_fundamentals: bool = False


class NewsAnalysisAgent(Agent):
    name = "news"
    model_tier = "sonnet"  # per-article; opus for synthesis in the PRD

    def _assess_article(self, raw: dict[str, Any], company: str) -> ArticleAssessment | None:
        prompt = (
            f"Company: {company}\n"
            f"Headline: {raw.get('title', '')}\n"
            f"Summary: {raw.get('summary', '')}\n"
            f"Source: {raw.get('source', '')}\n"
            f"Published: {raw.get('published_date', '')}"
        )
        try:
            assessment: ArticleAssessment = self._ask(  # type: ignore[assignment]
                _ARTICLE_SYSTEM, prompt, ArticleAssessment)
        except LLMError:
            return None
        # The feed, not the model, owns the factual metadata.
        assessment.title = raw.get("title", assessment.title)
        assessment.url = raw.get("url", assessment.url)
        assessment.source_name = raw.get("source", assessment.source_name)
        assessment.published_date = raw.get("published_date", assessment.published_date)
        return assessment

    def _synthesise(self, company: str, assessments: list[ArticleAssessment]) -> _NewsSynthesis:
        if not assessments:
            return _NewsSynthesis()
        lines = [
            f"- [{a.financial_impact.value}/{a.materiality}] {a.title}: {a.reasoning}"
            for a in assessments if a.scored
        ]
        prompt = f"Company: {company}\nAssessed items:\n" + "\n".join(lines)
        try:
            return self._synth_call(prompt)
        except LLMError:
            return _NewsSynthesis()

    def _synth_call(self, prompt: str) -> _NewsSynthesis:
        return self._ask(_SYNTHESIS_SYSTEM, prompt, _NewsSynthesis)  # type: ignore[return-value]

    def run(
        self,
        raw_articles: list[dict[str, Any]],
        *,
        company: str,
        lookback_days: int = 60,
        as_of: str | None = None,
    ) -> NewsAnalysis:
        assessments: list[ArticleAssessment] = []
        for raw in raw_articles:
            assessment = self._assess_article(raw, company)
            if assessment is not None:
                assessments.append(assessment)

        analysis = NewsAnalysis(lookback_days=lookback_days, as_of=as_of, articles=assessments)
        # The pillar score is computed in Python, never chosen by the model.
        aggregate = score_news(analysis)
        synthesis = self._synthesise(company, assessments)
        aggregate.top_positive = synthesis.top_positive
        aggregate.top_concerns = synthesis.top_concerns
        aggregate.themes = synthesis.themes
        aggregate.contradicts_fundamentals = synthesis.contradicts_fundamentals
        analysis.aggregate = aggregate
        return analysis
