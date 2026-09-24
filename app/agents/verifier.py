"""Verifier / Critic Agent (PRD 8.13).

Adversarially checks the generated narrative against the data layer. The
load-bearing check — numeric grounding — is deterministic Python
(``app/engine/verification``): every number in the prose is extracted and
matched against what the engine actually computed, and an unmatched number is
flagged as a candidate hallucination. The qualitative checks (uncited claims,
internal contradictions) are a best-effort LLM pass; if the model is
unavailable they are simply skipped, and the numeric gate still stands.

A report passes only if no number is ungrounded, the mandatory disclaimer is
present, and no contradiction was found. Failures are never suppressed — they
drive a single regeneration and, failing that, a visible unverified-claims
banner (PRD 8.13, veto V9).
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, Field

from app.agents.base import Agent
from app.agents.llm import LLMError
from app.engine.verification import (
    check_disclaimer_present,
    check_numeric_grounding,
    collect_known_values,
)
from app.models.report import ResearchReport, VerificationResult

_SEMANTIC_SYSTEM = (
    "You are adversarially reviewing an equity report's prose for two faults "
    "only: (1) qualitative claims about industry or news that carry no citation; "
    "(2) statements the prose makes that contradict the company's own reported "
    "data. List each concisely. Do not check numbers — that is done elsewhere."
)


class _SemanticCheck(BaseModel):
    uncited_claims: list[str] = Field(default_factory=list)
    contradictions: list[str] = Field(default_factory=list)


def report_prose(report: ResearchReport) -> str:
    """The LLM-generated text to verify — deliberately excludes the stamped
    disclaimer and any Python-rendered tables, which are not the model's claims."""
    parts: list[str] = list(report.executive_summary)
    parts.append(report.rating_rationale)
    parts.extend(s.body for s in report.sections)
    parts.extend(report.investment_view.supports_rating)
    parts.extend(report.investment_view.argues_against)
    parts.append(report.investment_view.bear_case)
    parts.extend(report.what_would_change_our_view.upgrade_triggers)
    parts.extend(report.what_would_change_our_view.downgrade_triggers)
    if report.analyst_caveat:
        parts.append(report.analyst_caveat)
    return "\n".join(p for p in parts if p)


class VerifierAgent(Agent):
    name = "verifier"
    model_tier = "opus"

    def __init__(self, llm: Any = None) -> None:
        # The LLM is optional: numeric grounding needs no model.
        self.llm = llm

    def verify(
        self,
        report: ResearchReport,
        *,
        metric_set: Any = None,
        statements: Any = None,
        market: Any = None,
        attempt: int = 1,
    ) -> VerificationResult:
        prose = report_prose(report)
        known = collect_known_values(report.decision, metric_set, statements, market)
        checked, unsupported = check_numeric_grounding(prose, known)
        disclaimer_ok = check_disclaimer_present(report.disclaimer)

        uncited: list[str] = []
        contradictions: list[str] = []
        notes: list[str] = []
        if self.llm is not None:
            try:
                semantic: _SemanticCheck = self.llm.complete_json(
                    system=_SEMANTIC_SYSTEM, user=prose, schema=_SemanticCheck)
                uncited = semantic.uncited_claims
                contradictions = semantic.contradictions
            except LLMError:
                notes.append("semantic check unavailable; numeric grounding only")

        passed = not unsupported and disclaimer_ok and not contradictions
        return VerificationResult(
            passed=passed, attempts=attempt,
            numeric_claims_checked=checked,
            unsupported_numbers=unsupported,
            uncited_claims=uncited,
            contradictions=contradictions,
            disclaimer_present=disclaimer_ok,
            notes=notes,
        )
