"""Track 3 — report quality by rubric + LLM-as-judge (PRD 19.3).

A generated report is scored against a 10-criterion rubric (weights from the
PRD) by an independent LLM judge. The judge emits only bounded 0-100 scores per
criterion; Python computes the weighted total — the same §6 discipline the whole
system uses, applied to its own evaluation. The judge goes through the LLM seam,
so this track is testable offline with a fake model.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import Agent
from app.eval.models import RubricCriterion, RubricScore, TrackResult
from app.report import render_markdown

RUBRIC: list[RubricCriterion] = [
    RubricCriterion(key="factual_accuracy", label="Factual accuracy of stated numbers", weight=0.20),
    RubricCriterion(key="logical_coherence", label="Logical coherence between evidence and rating", weight=0.15),
    RubricCriterion(key="balance", label="Balance — bear case stated fairly", weight=0.10),
    RubricCriterion(key="specificity", label="Specificity — no vague filler", weight=0.10),
    RubricCriterion(key="citation_quality", label="Citation quality and coverage", weight=0.10),
    RubricCriterion(key="analytical_depth", label="Analytical depth beyond restating ratios", weight=0.15),
    RubricCriterion(key="actionability", label="Actionability of the change-our-view triggers", weight=0.10),
    RubricCriterion(key="structure", label="Professional structure and readability", weight=0.05),
    RubricCriterion(key="hedging", label="Appropriate hedging of uncertainty", weight=0.05),
]

_SYSTEM = (
    "You are an independent examiner scoring an equity research report against a "
    "rubric. Score each criterion 0-100 on the report's own merits — do not be "
    "swayed by the recommendation. Be strict about factual accuracy, citation "
    "coverage and vague filler. Return only the scores."
)


class _RubricVerdict(BaseModel):
    factual_accuracy: int = Field(ge=0, le=100)
    logical_coherence: int = Field(ge=0, le=100)
    balance: int = Field(ge=0, le=100)
    specificity: int = Field(ge=0, le=100)
    citation_quality: int = Field(ge=0, le=100)
    analytical_depth: int = Field(ge=0, le=100)
    actionability: int = Field(ge=0, le=100)
    structure: int = Field(ge=0, le=100)
    hedging: int = Field(ge=0, le=100)


class ReportJudgeAgent(Agent):
    name = "report_judge"
    model_tier = "opus"

    def judge(self, report: Any) -> list[RubricScore]:
        verdict: _RubricVerdict = self._ask(_SYSTEM, render_markdown(report), _RubricVerdict)  # type: ignore[assignment]
        data = verdict.model_dump()
        return [RubricScore(key=c.key, score=int(data[c.key])) for c in RUBRIC]


def weighted_rubric_score(scores: list[RubricScore]) -> float:
    """Weighted 0-100 quality score from the per-criterion scores."""
    by_key = {s.key: s.score for s in scores}
    return round(sum(c.weight * by_key.get(c.key, 0) for c in RUBRIC), 2)


def run_rubric_track(report: Any, judge: ReportJudgeAgent) -> TrackResult:
    scores = judge.judge(report)
    total = weighted_rubric_score(scores)
    status = "PASS" if total >= 60.0 else "PARTIAL"
    details = [f"{c.label}: {next(s.score for s in scores if s.key == c.key)} (w {c.weight:.0%})"
               for c in RUBRIC]
    return TrackResult(
        track=3, name="Report Quality (Rubric + LLM Judge)", status=status,
        summary=f"Weighted report-quality score {total:.1f}/100 from an independent LLM judge.",
        metrics={"quality_score": total}, details=details)
