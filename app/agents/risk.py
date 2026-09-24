"""Risk Agent (PRD 8.10).

Scores risk across four dimensions as 0-100 levels (higher = riskier):
financial, business, valuation, governance. Also flags governance severity,
which feeds veto V6. The weighted risk level is computed in Python
(``app/engine/agent_scoring.score_risk``) and enters the composite inverted, so
more risk lowers the recommendation — but its principal job is the vetoes, not
the 5% marginal weight (PRD 12.1).

Governance flags are raised only where evidenced in the data or cited news,
never speculated (PRD 8.10).
"""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.engine.agent_scoring import score_risk
from app.models.agent_io import RiskAssessment

_SYSTEM = (
    "You are an equity risk analyst. Score four risk dimensions on a 0-100 scale "
    "where higher means MORE risk: financial_risk (leverage, coverage, "
    "liquidity), business_risk (concentration, cyclicality, competitive "
    "position, margin volatility), valuation_risk (multiple vs history/peers, "
    "implied vs delivered growth, downside to fair value), governance_flags "
    "(auditor changes, pledging, related-party dealings, restatements — only "
    "where evidenced). Set governance_severity to HIGH only for an evidenced, "
    "serious flag. List key_risks. Report levels only; do not compute an overall "
    "score."
)


class RiskAgent(Agent):
    name = "risk"
    model_tier = "opus"

    def run(self, context: dict[str, Any]) -> RiskAssessment:
        """``context`` is a compact dict of already-computed facts (key ratios,
        valuation upside, notable news) — the model reasons over facts, it does
        not fetch them."""
        lines = [f"{k}: {v}" for k, v in context.items()]
        user = "Assess risk from these facts:\n" + "\n".join(lines)
        assessment: RiskAssessment = self._ask(_SYSTEM, user, RiskAssessment)  # type: ignore[assignment]
        assessment.risk_score, _, _ = score_risk(assessment)
        return assessment
