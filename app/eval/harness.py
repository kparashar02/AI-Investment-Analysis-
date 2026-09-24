"""Evaluation harness orchestration + the docs/EVALUATION.md deliverable (PRD 19.8)."""

from __future__ import annotations

from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from app.config.settings import REPO_ROOT, config_stamp
from app.eval.models import EvaluationReport, TrackResult
from app.eval.tracks import (
    golden_decisions,
    run_determinism_track,
    run_directional_track,
    run_golden_track,
    run_groundedness_track,
    run_injection_track,
)

# An illustrative consensus reference for the synthetic set (PRD 19.2: consensus
# is a reference, not ground truth). Real evaluation supplies dated consensus.
_DEFAULT_CONSENSUS = {"ACME.NS": "POSITIVE", "LEVCYC.NS": "NEGATIVE"}


def _sample_report_items() -> list[tuple[Any, Any, Any, Any]]:
    """Build one report offline (fixed prose, no key) for the groundedness track."""
    from app.agents.llm import FakeLLM  # noqa: PLC0415
    from app.agents.narrative import NarrativeAgent  # noqa: PLC0415
    from app.agents.report_pipeline import produce_report  # noqa: PLC0415
    from app.agents.verifier import VerifierAgent  # noqa: PLC0415
    from app.data.fixtures import load_golden  # noqa: PLC0415
    from app.engine.registry import compute_metric_set  # noqa: PLC0415
    from app.engine.scoring import build_decision  # noqa: PLC0415

    golden = load_golden("acme_industries")
    metric_set = compute_metric_set(golden.statements, golden.market)
    decision = build_decision(metric_set, golden.statements, golden.market,
                              peer_multiples={"pe": [8, 9, 10, 11], "ev_ebitda": [7, 8, 9, 10]})
    draft = {
        "executive_summary": ["A high-quality franchise trading below its intrinsic value.",
                              "Cash conversion is consistently strong."],
        "rating_rationale": "Robust returns and conservative leverage support the rating.",
        "financial_commentary": "Return on equity stands at 32.39% and the shares trade at "
                                "about 10.5x earnings — a top-band return at an undemanding multiple.",
        "valuation_commentary": "The discounted cash flow points above the current price.",
        "risk_commentary": "Financial risk is low; cyclicality is the main concern.",
        "supports_rating": ["Top-band returns", "Low leverage"],
        "argues_against": ["Cyclical demand"], "bear_case": "A downturn would pressure margins.",
        "upgrade_triggers": ["Sustained margin expansion"], "downgrade_triggers": ["Falling order intake"],
        "monitorable_metrics": ["order intake", "EBITDA margin"],
    }
    report, decision = produce_report(
        decision, NarrativeAgent(FakeLLM([draft])), VerifierAgent(llm=None),
        metric_set=metric_set, statements=golden.statements, market=golden.market,
        as_of="2026-03-31", generated_at="2026-09-24T00:00:00Z")
    return [(report, metric_set, golden.statements, golden.market)]


def run_evaluation(*, judge: Any = None, consensus: dict[str, str] | None = None) -> EvaluationReport:
    """Run every offline-capable track. Track 3 runs only if an LLM ``judge`` is
    given; otherwise it is reported SKIPPED."""
    decisions = golden_decisions()
    rating_by_ticker = {t: d.rating for t, d in decisions.items()}
    report_items = _sample_report_items()

    tracks: list[TrackResult] = [
        run_golden_track(),
        run_directional_track(rating_by_ticker, consensus or _DEFAULT_CONSENSUS),
    ]

    if judge is not None:
        from app.eval.rubric import run_rubric_track  # noqa: PLC0415
        tracks.append(run_rubric_track(report_items[0][0], judge))
    else:
        tracks.append(TrackResult(
            track=3, name="Report Quality (Rubric + LLM Judge)", status="SKIPPED",
            summary="Needs an LLM judge (pass one to run_evaluation, or `eval --judge`)."))

    tracks += [
        run_determinism_track(),
        run_groundedness_track(report_items),
        run_injection_track(),
    ]
    tracks.sort(key=lambda t: t.track)

    return EvaluationReport(
        generated_at=datetime.now(timezone.utc).isoformat(timespec="seconds"),
        config_versions=config_stamp(), tracks=tracks)


def render_evaluation_md(report: EvaluationReport) -> str:
    lines = [
        "# Evaluation Report", "",
        "> Six-track evaluation of the Agentic Equity Research Analyst (PRD §19). "
        "Educational / academic project — not investment advice.", "",
        f"Generated: {report.generated_at}",
        f"Config: weights {report.config_versions.get('weights_version')}, "
        f"thresholds {report.config_versions.get('thresholds_version')}, "
        f"valuation {report.config_versions.get('valuation_version')}", "",
        "## Summary", "",
        "| Track | Name | Status | Summary |", "|---|---|---|---|",
    ]
    for t in report.tracks:
        lines.append(f"| {t.track} | {t.name} | **{t.status}** | {t.summary} |")
    lines += ["", f"**Overall: {'PASS' if report.passed else 'FAIL'}** "
              "(PARTIAL/SKIPPED tracks are informational, not failures).", ""]

    for t in report.tracks:
        lines += ["", f"## Track {t.track} — {t.name}", "", f"**Status: {t.status}.** {t.summary}"]
        if t.metrics:
            lines.append("")
            lines.append(" · ".join(f"{k}: {v:g}" for k, v in t.metrics.items()))
        if t.details:
            lines.append("")
            lines += [f"- {d}" for d in t.details]

    lines += [
        "", "## Notes on scope", "",
        "- **Track 1** validates the engine's arithmetic against the synthetic "
        "closed-form golden values. Validation against a *real* company's filing "
        "requires a REAL golden hand-entered from an annual report (`data/golden/TEMPLATE.csv`); "
        "that is the one honest gap remaining.",
        "- **Track 2** runs on the deterministic decisions with an illustrative "
        "consensus; a full run supplies dated consensus for the 15-company set.",
        "- **Track 3** requires an LLM judge and a live report.",
        "- **Track 4 Method B** (fresh-run σ across live LLM runs) uses `variance_stats()`; "
        "the 75%-weight deterministic pillars show zero variance by construction.",
        "",
    ]
    return "\n".join(lines) + "\n"


def write_evaluation_md(report: EvaluationReport, path: str | Path | None = None) -> Path:
    out = Path(path) if path is not None else (REPO_ROOT / "docs" / "EVALUATION.md")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(render_evaluation_md(report), encoding="utf-8")
    return out
