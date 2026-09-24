"""Evaluation harness (app/eval) — all six tracks, offline."""

from __future__ import annotations

import pytest

from app.agents.llm import FakeLLM
from app.agents.narrative import NarrativeAgent
from app.agents.report_pipeline import produce_report
from app.agents.verifier import VerifierAgent
from app.engine.scoring import build_decision
from app.eval.harness import render_evaluation_md, run_evaluation, write_evaluation_md
from app.eval.rubric import ReportJudgeAgent, run_rubric_track, weighted_rubric_score
from app.eval.tracks import (
    rating_class,
    run_determinism_track,
    run_directional_track,
    run_golden_track,
    run_groundedness_track,
    run_injection_track,
    variance_stats,
)
from app.models.decision import Rating


@pytest.fixture
def report_item(acme, acme_metrics):
    decision = build_decision(acme_metrics, acme.statements, acme.market,
                              peer_multiples={"pe": [8, 9, 10, 11]})
    draft = {"executive_summary": ["A quality franchise below fair value."],
             "rating_rationale": "Strong returns support the rating.",
             "risk_commentary": "Low leverage; cyclicality is the main risk."}
    report, _ = produce_report(decision, NarrativeAgent(FakeLLM([draft])), VerifierAgent(llm=None),
                               metric_set=acme_metrics, statements=acme.statements, market=acme.market)
    return (report, acme_metrics, acme.statements, acme.market)


# --- Track 1 ---------------------------------------------------------------

def test_golden_track_passes():
    result = run_golden_track()
    assert result.status == "PASS"
    assert result.metrics["metrics_checked"] > 0
    assert result.metrics["failures"] == 0


# --- Track 2 ---------------------------------------------------------------

def test_rating_class_mapping():
    assert rating_class(Rating.BUY) == "POSITIVE"
    assert rating_class(Rating.ACCUMULATE) == "POSITIVE"
    assert rating_class(Rating.HOLD) == "NEUTRAL"
    assert rating_class(Rating.SELL) == "NEGATIVE"
    assert rating_class(Rating.NO_RATING) == "NO_RATING"


def test_directional_track_counts_agreement():
    result = run_directional_track(
        {"A.NS": Rating.BUY, "B.NS": Rating.SELL},
        {"A.NS": "POSITIVE", "B.NS": "POSITIVE"})
    assert result.status == "PARTIAL"                 # informational, not pass/fail
    assert result.metrics["agreements"] == 1.0
    assert result.metrics["companies"] == 2.0
    assert any("B.NS" in d for d in result.details)   # disagreement surfaced


# --- Track 3 ---------------------------------------------------------------

def _verdict(score=80):
    keys = ["factual_accuracy", "logical_coherence", "balance", "specificity",
            "citation_quality", "analytical_depth", "actionability", "structure", "hedging"]
    return {k: score for k in keys}


def test_weighted_rubric_score():
    from app.eval.models import RubricScore
    scores = [RubricScore(key=k, score=v) for k, v in _verdict(70).items()]
    assert weighted_rubric_score(scores) == pytest.approx(70.0)   # weights sum to 1


def test_rubric_track_with_fake_judge(report_item):
    report = report_item[0]
    judge = ReportJudgeAgent(FakeLLM([_verdict(85)]))
    result = run_rubric_track(report, judge)
    assert result.metrics["quality_score"] == pytest.approx(85.0)
    assert result.status == "PASS"


# --- Track 4 ---------------------------------------------------------------

def test_determinism_track_passes():
    result = run_determinism_track(runs=5)
    assert result.status == "PASS"
    assert result.metrics["unstable"] == 0.0


def test_variance_stats():
    assert variance_stats([82.5, 82.5, 82.5])["stdev"] == 0.0
    s = variance_stats([80.0, 82.0, 84.0])
    assert s["mean"] == pytest.approx(82.0)
    assert s["stdev"] > 0


# --- Track 5 ---------------------------------------------------------------

def test_groundedness_track_finds_no_unflagged(report_item):
    result = run_groundedness_track([report_item])
    assert result.status == "PASS"
    assert result.metrics["unflagged"] == 0.0


# --- Track 6 ---------------------------------------------------------------

def test_injection_track_passes():
    result = run_injection_track()
    assert result.status == "PASS"
    assert result.metrics["failures"] == 0.0
    # the three adversarial properties are all recorded
    assert all(d.startswith("ok:") for d in result.details)


# --- harness ---------------------------------------------------------------

def test_run_evaluation_covers_all_six_tracks():
    report = run_evaluation()
    assert [t.track for t in report.tracks] == [1, 2, 3, 4, 5, 6]
    assert report.passed is True                       # no track FAILED
    assert report.tracks[2].status == "SKIPPED"        # Track 3 has no judge


def test_evaluation_markdown_and_file(tmp_path):
    report = run_evaluation()
    md = render_evaluation_md(report)
    assert "# Evaluation Report" in md
    assert "Track 6 — Adversarial" in md
    out = write_evaluation_md(report, tmp_path / "EVAL.md")
    assert out.exists() and "Track 1" in out.read_text(encoding="utf-8")
