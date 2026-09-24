"""Narrative → verify → regenerate loop (PRD 8.13).

Produces the report and adversarially verifies it. If verification fails, the
narrative is regenerated once with the specific problems fed back. If it still
fails, the report ships with a visible unverified-claims banner and the
decision's confidence is downgraded (veto V9) — the failure is surfaced, never
silently suppressed.

The rating itself is never touched: V9 lowers *confidence*, and the banner tells
the reader some prose could not be grounded. The computed recommendation stands.
"""

from __future__ import annotations

from typing import Any

from app.agents.narrative import NarrativeAgent
from app.agents.verifier import VerifierAgent
from app.models.decision import Confidence, InvestmentDecision
from app.models.report import ResearchReport, VerificationResult


def _feedback(result: VerificationResult) -> str:
    lines: list[str] = []
    if result.unsupported_numbers:
        lines.append("Numbers not found in the data (remove or correct them): "
                     + "; ".join(f"'{n.value}' ({n.context})" for n in result.unsupported_numbers))
    if result.contradictions:
        lines.append("Contradictions with the data: " + "; ".join(result.contradictions))
    if not result.disclaimer_present:
        lines.append("The educational-use disclaimer is missing.")
    return "\n".join(lines)


def _apply_v9(decision: InvestmentDecision) -> InvestmentDecision:
    """Double verification failure → downgrade confidence and record veto V9."""
    vetoes = []
    for veto in decision.vetoes:
        if veto.code == "V9":
            vetoes.append(veto.model_copy(update={
                "evaluable": True, "triggered": True,
                "note": "report failed verification twice; confidence downgraded"}))
        else:
            vetoes.append(veto)
    applied = decision.applied_vetoes if "V9" in decision.applied_vetoes else [*decision.applied_vetoes, "V9"]
    warnings = [*decision.warnings,
                "Report failed automated verification twice; some prose could not be "
                "grounded in the data. Confidence downgraded (veto V9); see the "
                "unverified-claims banner."]
    return decision.model_copy(update={
        "confidence": Confidence.LOW, "vetoes": vetoes,
        "applied_vetoes": applied, "warnings": warnings})


def produce_report(
    decision: InvestmentDecision,
    narrative_agent: NarrativeAgent,
    verifier: VerifierAgent,
    *,
    metric_set: Any = None,
    statements: Any = None,
    market: Any = None,
    industry: Any = None,
    news: Any = None,
    as_of: str | None = None,
    generated_at: str | None = None,
    max_attempts: int = 2,
) -> tuple[ResearchReport, InvestmentDecision]:
    """Write and verify the report; returns ``(report, decision)`` where the
    decision may have been V9-downgraded."""
    feedback: str | None = None
    report: ResearchReport | None = None
    result: VerificationResult | None = None

    for attempt in range(1, max_attempts + 1):
        report = narrative_agent.run(
            decision, industry=industry, news=news,
            as_of=as_of, generated_at=generated_at, feedback=feedback)
        result = verifier.verify(
            report, metric_set=metric_set, statements=statements, market=market, attempt=attempt)
        report.verification = result
        if result.passed:
            break
        feedback = _feedback(result)

    assert report is not None and result is not None
    final_decision = decision
    if not result.passed:
        report.unverified_banner = True
        final_decision = _apply_v9(decision)
        report.decision = final_decision       # keep the report's embedded decision consistent
    return report, final_decision
