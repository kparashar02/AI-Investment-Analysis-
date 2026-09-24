"""Industry, risk, valuation-assumption and peer agents (offline via FakeLLM)."""

from __future__ import annotations

import pytest

from app.agents.industry import IndustryAgent
from app.agents.llm import FakeLLM
from app.agents.peer import PeerComparisonAgent, _PeerReview, _PeerVerdict
from app.agents.risk import RiskAgent
from app.agents.valuation_assumptions import ValuationAssumptionsAgent


# --- industry --------------------------------------------------------------

def test_industry_agent_fills_computed_score():
    llm = FakeLLM([{"industry_growth_outlook": 4, "competitive_intensity": 2,
                    "regulatory_risk": 2, "cyclicality": 3}])
    assessment = IndustryAgent(llm).run("Capital Goods", "Industrial Machinery")
    assert assessment.industry_growth_outlook == 4
    assert assessment.industry_score == pytest.approx(70.0)   # computed in Python


# --- risk ------------------------------------------------------------------

def test_risk_agent_fills_computed_score():
    llm = FakeLLM([{"financial_risk": 30, "business_risk": 40,
                    "valuation_risk": 50, "governance_flags": 20}])
    assessment = RiskAgent(llm).run({"debt_to_equity": 0.4})
    assert assessment.risk_score == pytest.approx(36.0)


# --- valuation assumptions -------------------------------------------------

def test_valuation_assumptions_clamps_terminal_and_pads_path():
    # terminal 7.0 is above the 5.5 ceiling; path is short of the 5y horizon.
    llm = FakeLLM([{"revenue_growth_path_pct": [15.0, 12.0, 9.0], "terminal_growth_pct": 7.0}])
    assumptions = ValuationAssumptionsAgent(llm).run({"revenue_cagr_pct": 12.0})
    assert assumptions.terminal_growth_pct == pytest.approx(5.5)   # clamped
    assert len(assumptions.revenue_growth_path_pct) == 5           # padded to horizon
    assert assumptions.revenue_growth_path_pct[-1] == pytest.approx(9.0)  # held last value
    assert assumptions.guardrail_notes                              # corrections recorded


def test_valuation_assumptions_clamps_wild_growth_rate():
    llm = FakeLLM([{"revenue_growth_path_pct": [200.0, 10, 8, 6, 5], "terminal_growth_pct": 5.0}])
    assumptions = ValuationAssumptionsAgent(llm).run({})
    assert assumptions.revenue_growth_path_pct[0] == pytest.approx(40.0)  # clamped to band


# --- peer ------------------------------------------------------------------

def test_peer_agent_computes_multiples(acme):
    # Fake fetch: every candidate returns the acme fixture, so 3 valid peers.
    fetch = lambda t: (acme.statements, acme.market)  # noqa: E731
    agent = PeerComparisonAgent(FakeLLM([]))   # no review queued -> keep all
    comparison = agent.run("ACME.NS", ["P1.NS", "P2.NS", "P3.NS"], fetch)
    assert comparison.valid_peer_count == 3
    assert "pe" in comparison.peer_multiples
    assert len(comparison.peer_multiples["pe"]) == 3


def test_peer_agent_review_can_reject(acme):
    fetch = lambda t: (acme.statements, acme.market)  # noqa: E731
    review = _PeerReview(verdicts=[_PeerVerdict(ticker="P2.NS", keep=False, reason="holding company")])
    agent = PeerComparisonAgent(FakeLLM([review]))
    comparison = agent.run("ACME.NS", ["P1.NS", "P2.NS", "P3.NS"], fetch)
    assert comparison.valid_peer_count == 2
    rejected = [p for p in comparison.peers if not p.accepted]
    assert rejected[0].ticker == "P2.NS"


def test_peer_agent_handles_unfetchable_peer(acme):
    def fetch(ticker):
        if ticker == "BAD.NS":
            return None, None
        return acme.statements, acme.market
    agent = PeerComparisonAgent(FakeLLM([]))
    comparison = agent.run("ACME.NS", ["P1.NS", "BAD.NS", "P3.NS"], fetch)
    assert comparison.valid_peer_count == 2
    assert any("V10" in n for n in comparison.notes)   # 2 < 3 -> V10 note raised
