"""Phase 4 — narrative, verification and report (offline).

Covers the deterministic numeric grounding, the narrative agent's inability to
change the rating, the verify→regenerate→V9 loop, and the Markdown renderer.
"""

from __future__ import annotations

import pytest

from app.agents.llm import FakeLLM
from app.agents.narrative import NarrativeAgent, _NarrativeDraft
from app.agents.report_pipeline import produce_report
from app.agents.verifier import VerifierAgent
from app.engine.scoring import build_decision
from app.engine.verification import (
    check_disclaimer_present,
    check_numeric_grounding,
    collect_known_values,
    extract_financial_numbers,
)
from app.models.decision import Confidence, Rating
from app.report import render_markdown


@pytest.fixture
def decision(acme, acme_metrics):
    return build_decision(acme_metrics, acme.statements, acme.market)


# --- numeric grounding -----------------------------------------------------

def test_extract_ignores_years_and_small_counts():
    values = [v for _, v in extract_financial_numbers("In 2026 we saw 3 deals close.")]
    assert values == []      # a year and a small count are not financial claims


def test_extract_catches_financial_figures():
    tokens = dict(extract_financial_numbers("ROE was 32.4%, debt ₹1,200 crore, trading at 10.5x."))
    assert 32.4 in tokens.values()
    assert 1200.0 in tokens.values()
    assert 10.5 in tokens.values()


def test_grounding_flags_a_hallucinated_number(decision, acme, acme_metrics):
    known = collect_known_values(decision, acme_metrics, acme.statements, acme.market)
    # A figure far from any computed value (scores densely fill 0-100, so the
    # test uses a clearly-absent number to isolate the grounding logic).
    checked, unsupported = check_numeric_grounding("The company posted an ROE of 250.0%.", known)
    assert checked == 1
    assert len(unsupported) == 1
    assert unsupported[0].value.startswith("250")


def test_grounding_passes_a_real_number(decision, acme, acme_metrics):
    known = collect_known_values(decision, acme_metrics, acme.statements, acme.market)
    roe = acme_metrics.value("roe")
    _, unsupported = check_numeric_grounding(f"ROE stands at {roe:.2f}%.", known)
    assert unsupported == []


def test_disclaimer_detection():
    assert check_disclaimer_present("... this is not investment advice; educational use only.")
    assert not check_disclaimer_present("A perfectly ordinary sentence.")


# --- narrative agent -------------------------------------------------------

def _draft(**kw) -> dict:
    base = dict(executive_summary=["Solid business."], rating_rationale="Explains the rating.",
                supports_rating=["Strong ROE"], bear_case="Cyclical risk.",
                upgrade_triggers=["margin expansion"], analyst_caveat=None)
    base.update(kw)
    return base


def test_narrative_cannot_change_the_rating(decision):
    # Even if the model 'wants' a different call, the report's rating is the
    # decision's — there is no field for it to write one.
    agent = NarrativeAgent(FakeLLM([_draft()]))
    report = agent.run(decision)
    assert report.decision.rating is decision.rating
    assert "rating" not in _NarrativeDraft.model_fields   # no rating field to write
    assert report.disclaimer


def test_narrative_surfaces_analyst_caveat(decision):
    agent = NarrativeAgent(FakeLLM([_draft(analyst_caveat="I read the growth pillar as optimistic.")]))
    report = agent.run(decision)
    assert report.analyst_caveat == "I read the growth pillar as optimistic."


# --- verify -> regenerate -> V9 loop --------------------------------------

def test_clean_report_passes_first_attempt(decision):
    narrative = NarrativeAgent(FakeLLM([_draft()]))     # no numbers -> grounds clean
    verifier = VerifierAgent(llm=None)                   # numeric-only
    report, final = produce_report(decision, narrative, verifier)
    assert report.verification.passed is True
    assert report.verification.attempts == 1
    assert report.unverified_banner is False
    assert final.confidence is decision.confidence       # not downgraded


def test_double_failure_sets_banner_and_v9(decision):
    # Both drafts contain an ungrounded number, so verification fails twice.
    bad = _draft(rating_rationale="Return on equity is a stellar 250.0%.")
    narrative = NarrativeAgent(FakeLLM([bad, bad]))
    verifier = VerifierAgent(llm=None)
    report, final = produce_report(decision, narrative, verifier, max_attempts=2)
    assert report.verification.passed is False
    assert report.verification.attempts == 2
    assert report.unverified_banner is True
    assert final.confidence is Confidence.LOW            # V9 downgrade
    assert "V9" in final.applied_vetoes
    assert report.decision.rating is decision.rating     # rating still unchanged


# --- markdown renderer -----------------------------------------------------

def test_markdown_has_all_eighteen_sections(decision):
    report = NarrativeAgent(FakeLLM([_draft()])).run(decision)
    md = render_markdown(report)
    for n in range(1, 19):
        assert f"## {n}." in md
    assert "not investment advice" in md.lower()          # disclaimer present
    assert decision.rating.value in md or "NO RATING" in md


def test_markdown_shows_unverified_banner_when_set(decision):
    report = NarrativeAgent(FakeLLM([_draft()])).run(decision)
    report.unverified_banner = True
    md = render_markdown(report)
    assert "UNVERIFIED CLAIMS" in md
