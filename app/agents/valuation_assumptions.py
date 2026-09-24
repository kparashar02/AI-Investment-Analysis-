"""Valuation Assumption Agent (PRD 8.9).

The model's job is narrow and genuinely linguistic: propose a revenue growth
*fade* path and a terminal growth rate, justified from the industry picture and
history. Python then validates that proposal against the hard guardrails
(terminal growth inside the configured band, path length matching the horizon,
individual growth rates sane) and records any correction. The DCF arithmetic
itself is untouched by the model (PRD 8.9 division of labour).
"""

from __future__ import annotations

from typing import Any

from app.agents.base import Agent
from app.config.settings import load_valuation
from app.engine.helpers import clamp
from app.models.agent_io import ValuationAssumptions

_SYSTEM = (
    "You are a valuation analyst proposing DCF assumptions. Propose a revenue "
    "growth fade path as a list of annual percentages over the forecast horizon "
    "(e.g. [12, 10, 8, 6, 5]) that starts near recent performance and fades "
    "toward a sustainable long-run rate, justified by the industry outlook. "
    "Propose terminal_growth_pct within 3.0-5.5 (India nominal long-run) and an "
    "ebit_margin if you expect it to differ from the latest reported margin. "
    "Explain briefly in rationale. Propose only; the engine validates and computes."
)


class ValuationAssumptionsAgent(Agent):
    name = "valuation_assumptions"
    model_tier = "opus"

    def run(self, context: dict[str, Any], config: dict[str, Any] | None = None) -> ValuationAssumptions:
        cfg = config or load_valuation()
        horizon = int((cfg.get("forecast") or {}).get("horizon_years", 5))
        lines = [f"{k}: {v}" for k, v in context.items()]
        user = (
            f"Forecast horizon: {horizon} years.\nContext:\n" + "\n".join(lines)
            + "\n\nPropose the growth fade path and terminal growth."
        )
        assumptions: ValuationAssumptions = self._ask(_SYSTEM, user, ValuationAssumptions)  # type: ignore[assignment]
        return self._validate(assumptions, cfg, horizon)

    def _validate(
        self, assumptions: ValuationAssumptions, cfg: dict[str, Any], horizon: int,
    ) -> ValuationAssumptions:
        """Enforce the DCF guardrails on the model's proposal (PRD 8.9)."""
        tg = cfg.get("terminal_growth") or {}
        g_min, g_max = float(tg.get("min_pct", 3.0)), float(tg.get("max_pct", 5.5))
        notes: list[str] = []

        original_g = assumptions.terminal_growth_pct
        bounded_g = clamp(original_g, g_min, g_max)
        if bounded_g != original_g:
            notes.append(f"terminal growth {original_g}% clamped to the {g_min}-{g_max}% band")
            assumptions.terminal_growth_pct = bounded_g

        path = list(assumptions.revenue_growth_path_pct)
        if not path:
            notes.append("empty growth path proposed; the DCF will fall back to its deterministic default")
        else:
            # Match the path to the horizon: truncate, or extend by holding the last value.
            if len(path) > horizon:
                notes.append(f"growth path trimmed from {len(path)} to the {horizon}-year horizon")
                path = path[:horizon]
            elif len(path) < horizon:
                notes.append(f"growth path extended to {horizon} years by holding the final rate")
                path = path + [path[-1]] * (horizon - len(path))
            # Clamp implausible individual rates to a sane band.
            clamped = [clamp(g, -20.0, 40.0) for g in path]
            if clamped != path:
                notes.append("one or more growth rates were clamped to the -20%..40% plausibility band")
            path = clamped
        assumptions.revenue_growth_path_pct = path
        assumptions.guardrail_notes = notes
        return assumptions
