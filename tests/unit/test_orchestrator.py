"""End-to-end pipeline (app/agents/orchestrator.py) via LangGraph, offline.

The whole graph runs with a RoutedFakeLLM (canned agent outputs keyed by schema,
so parallel execution order is irrelevant) and a fake data service returning the
acme fixture. No network, no API key.
"""

from __future__ import annotations

import pytest

from app.agents.llm import RoutedFakeLLM
from app.agents.orchestrator import AnalysisPipeline
from app.models.decision import Rating, RatingBasis


class FakeDataService:
    def __init__(self, statements, market, *, fail_statements=False):
        self._s = statements
        self._m = market
        self._fail = fail_statements

    def get_statements(self, ticker, **kwargs):
        if self._fail:
            raise RuntimeError(f"no statements for {ticker}")
        return self._s

    def get_market_or_none(self, ticker):
        return self._m


def _full_llm() -> RoutedFakeLLM:
    return RoutedFakeLLM({
        "CompanyIdentity": [{
            "ticker": "ACME.NS", "legal_name": "Acme Industries",
            "sector": "Capital Goods", "industry": "Industrial Machinery",
            "candidate_peers": ["P1.NS", "P2.NS", "P3.NS"],
        }],
        "ArticleAssessment": [
            {"title": "t", "relevance": "HIGH", "source_tier": 1,
             "financial_impact": "POSITIVE", "materiality": 4, "confidence": 0.9},
            {"title": "t", "relevance": "MEDIUM", "source_tier": 1,
             "financial_impact": "NEGATIVE", "materiality": 3, "confidence": 0.7},
        ],
        "IndustryAssessment": [{"industry_growth_outlook": 4, "competitive_intensity": 2,
                                "regulatory_risk": 2, "cyclicality": 3}],
        "ValuationAssumptions": [{"revenue_growth_path_pct": [15, 12, 9, 7, 5],
                                  "terminal_growth_pct": 5.0}],
        "RiskAssessment": [{"financial_risk": 25, "business_risk": 30,
                            "valuation_risk": 35, "governance_flags": 15}],
        # Number-free narrative prose, so the verifier grounds it cleanly.
        "_NarrativeDraft": [{
            "executive_summary": ["Healthy returns and strong cash generation.",
                                  "Valuation looks reasonable against peers."],
            "rating_rationale": "The rating reflects robust fundamentals and fair valuation.",
            "financial_commentary": "Margins are strong and improving.",
            "valuation_commentary": "The company trades near its intrinsic value.",
            "risk_commentary": "Leverage is modest and coverage is comfortable.",
            "supports_rating": ["Strong return ratios", "Low leverage"],
            "argues_against": ["Cyclical end-markets"],
            "bear_case": "A demand downturn would pressure margins.",
            "upgrade_triggers": ["Sustained margin expansion"],
            "downgrade_triggers": ["A sharp fall in order intake"],
            "monitorable_metrics": ["quarterly revenue growth", "EBITDA margin"],
            "analyst_caveat": None,
        }],
    })


def _articles(identity):
    return [
        {"title": "Acme wins order", "summary": "big deal", "source": "Mint",
         "url": "u1", "published_date": "2026-03-31"},
        {"title": "Sector cost pressure", "summary": "wages", "source": "ET",
         "url": "u2", "published_date": "2026-03-31"},
    ]


def _pipeline(acme, llm=None, fail_statements=False):
    ds = FakeDataService(acme.statements, acme.market, fail_statements=fail_statements)
    return AnalysisPipeline(
        llm or _full_llm(), ds,
        news_source=_articles,
        peer_fetch=lambda t: (acme.statements, acme.market),
    )


_POSITIVE = {Rating.BUY, Rating.ACCUMULATE}


def test_full_pipeline_produces_a_full_decision(acme):
    state = _pipeline(acme).run("Acme Industries")
    decision = state["decision"]
    assert decision.basis is RatingBasis.FULL
    assert len(decision.pillars_scored) == 7
    assert decision.rating in _POSITIVE      # healthy company, mixed news
    assert state["errors"] == []


def test_pipeline_ran_every_node(acme):
    state = _pipeline(acme).run("Acme Industries")
    trace = set(state["trace"])
    assert {"resolve", "fetch_data", "compute_metrics", "news", "industry",
            "peer", "valuation_assumptions", "risk", "decide", "report"} <= trace
    assert "abort" not in trace


def test_pipeline_produces_a_verified_report(acme):
    state = _pipeline(acme).run("Acme Industries")
    report = state["report"]
    assert report is not None
    assert report.verification.passed is True        # number-free prose grounds cleanly
    assert report.unverified_banner is False
    assert report.disclaimer                          # SEBI disclaimer stamped
    # The report's rating equals the decision's — the narrative cannot change it.
    assert report.decision.rating is state["decision"].rating


def test_pipeline_populates_all_agent_outputs(acme):
    state = _pipeline(acme).run("Acme Industries")
    assert state["identity"].ticker == "ACME.NS"
    assert state["news"].aggregate is not None
    assert state["industry"].industry_score is not None
    assert state["risk"].risk_score is not None
    assert state["peer"].valid_peer_count == 3
    assert state["assumptions"].revenue_growth_path_pct


def test_analyze_convenience_returns_decision(acme):
    decision = _pipeline(acme).analyze("Acme Industries")
    assert decision.rating in _POSITIVE


# --- failure handling ------------------------------------------------------

def test_critical_data_failure_aborts_with_no_rating(acme):
    state = _pipeline(acme, fail_statements=True).run("Acme Industries")
    decision = state["decision"]
    assert decision.rating is Rating.NO_RATING
    assert decision.basis is RatingBasis.NO_RATING
    assert "abort" in state["trace"]
    assert any("critical" in e for e in state["errors"])


def test_non_critical_agent_failure_degrades_to_quant_core(acme):
    # Omit the IndustryAssessment response: the industry agent fails, the node
    # records the gap, and the pipeline still produces a rating (one pillar short).
    llm = _full_llm()
    llm._by_schema.pop("IndustryAssessment")
    state = _pipeline(acme, llm=llm).run("Acme Industries")
    decision = state["decision"]
    assert state["industry"] is None
    assert any("industry" in e for e in state["errors"])
    assert decision.basis is RatingBasis.QUANT_CORE
    assert "industry" in decision.pillars_missing
    assert decision.rating is not Rating.NO_RATING   # still rated on what survived


class _FakeWebResearch:
    def research(self, sector, industry, *, as_of=None):
        from app.data.web_research import WebResearch
        from app.models.agent_io import Citation
        return WebResearch(
            snippets=["<source domain='ibef.org' tier='2'>Capital goods demand is firm.</source>"],
            citations=[Citation(url="https://ibef.org/industry/engineering", source="ibef.org",
                                tier=2, access_date="2026-09-24", claim="Capital goods demand is firm.")])


def test_web_research_supplies_authoritative_citations(acme):
    ds = FakeDataService(acme.statements, acme.market)
    pipeline = AnalysisPipeline(
        _full_llm(), ds, news_source=_articles,
        peer_fetch=lambda t: (acme.statements, acme.market),
        web_research=_FakeWebResearch())
    state = pipeline.run("Acme Industries")
    citations = state["industry"].citations
    # The citation is the allowlisted source actually fetched, not an LLM echo.
    assert len(citations) == 1
    assert "ibef.org" in citations[0] and "Tier 2" in citations[0]


@pytest.mark.determinism
def test_pipeline_is_deterministic(acme):
    a = _pipeline(acme).analyze("Acme Industries").fingerprint()
    b = _pipeline(acme).analyze("Acme Industries").fingerprint()
    assert a == b
