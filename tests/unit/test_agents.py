"""Symbol resolution and news agents (app/agents), driven by FakeLLM offline."""

from __future__ import annotations

import pytest

from app.agents.llm import FakeLLM
from app.agents.news import NewsAnalysisAgent, _NewsSynthesis
from app.agents.symbol_resolution import SymbolResolutionAgent
from app.models.agent_io import ArticleAssessment, FinancialImpact, Relevance


# --- symbol resolution -----------------------------------------------------

def test_symbol_resolution_returns_identity():
    llm = FakeLLM([{"ticker": "TCS.NS", "ticker_nse": "TCS.NS",
                    "legal_name": "Tata Consultancy Services", "sector": "IT Services"}])
    identity = SymbolResolutionAgent(llm).run("Tata Consultancy")
    assert identity.ticker == "TCS.NS"
    assert identity.sector == "IT Services"


def test_symbol_resolution_backfills_missing_ticker():
    # Model left ticker blank but gave the NSE ticker; Python fills the invariant.
    llm = FakeLLM([{"ticker": "", "ticker_nse": "INFY.NS", "legal_name": "Infosys"}])
    identity = SymbolResolutionAgent(llm).run("infosys")
    assert identity.ticker == "INFY.NS"


# --- news agent ------------------------------------------------------------

def _assessment(**kw) -> dict:
    base = dict(title="llm-echoed title", relevance="HIGH", source_tier=1,
                financial_impact="POSITIVE", materiality=4, confidence=0.9, reasoning="r")
    base.update(kw)
    return base


def _raw(title, date="2026-03-31", **kw):
    return {"title": title, "summary": "s", "source": "Mint", "url": "http://x",
            "published_date": date, **kw}


def test_news_agent_assesses_scores_and_synthesises():
    llm = FakeLLM([
        _assessment(financial_impact="POSITIVE", materiality=4, confidence=0.87),
        _assessment(financial_impact="NEGATIVE", materiality=3, confidence=0.74,
                    relevance="MEDIUM"),
        _NewsSynthesis(top_positive=["big deal win"], top_concerns=["wage pressure"],
                       themes=["demand"], contradicts_fundamentals=False),
    ])
    agent = NewsAnalysisAgent(llm)
    analysis = agent.run([_raw("A: deal win"), _raw("B: wage pressure")],
                         company="TCS", as_of="2026-03-31")
    assert len(analysis.articles) == 2
    # News score is computed in Python (matches the PRD worked example ~61.05).
    assert analysis.aggregate.news_score == pytest.approx(61.05, abs=0.1)
    # Synthesis merged in.
    assert analysis.aggregate.top_positive == ["big deal win"]


def test_news_agent_overwrites_metadata_from_feed():
    llm = FakeLLM([_assessment(title="MODEL TITLE"), _NewsSynthesis()])
    agent = NewsAnalysisAgent(llm)
    analysis = agent.run([_raw("FEED TITLE", date="2026-02-01")],
                         company="TCS", as_of="2026-03-31")
    article = analysis.articles[0]
    assert article.title == "FEED TITLE"          # feed wins over the model
    assert article.published_date == "2026-02-01"
    assert article.source_name == "Mint"


def test_news_agent_skips_articles_the_model_cannot_assess():
    # Two raw articles; the model fails on the second (an LLM error). That
    # article is skipped rather than crashing the whole analysis.
    from app.agents.llm import LLMError

    llm = FakeLLM([_assessment(), LLMError("model failed on B"), _NewsSynthesis()])
    agent = NewsAnalysisAgent(llm)
    analysis = agent.run([_raw("A"), _raw("B")], company="TCS", as_of="2026-03-31")
    assert len(analysis.articles) == 1            # second article skipped


def test_news_agent_empty_feed_is_neutral():
    llm = FakeLLM([])   # no calls should be made
    analysis = NewsAnalysisAgent(llm).run([], company="TCS", as_of="2026-03-31")
    assert analysis.aggregate.insufficient_coverage is True
    assert analysis.aggregate.news_score == 50.0


def test_news_agent_returns_article_assessments():
    llm = FakeLLM([_assessment(), _NewsSynthesis()])
    analysis = NewsAnalysisAgent(llm).run([_raw("A")], company="TCS", as_of="2026-03-31")
    assert isinstance(analysis.articles[0], ArticleAssessment)
    assert analysis.articles[0].relevance is Relevance.HIGH
    assert analysis.articles[0].financial_impact is FinancialImpact.POSITIVE
