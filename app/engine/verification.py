"""Deterministic numeric grounding (PRD 8.13 check 1).

The verifier's first and most important check — does every number in the prose
trace to a real computed value? — is pure arithmetic, so it lives here in the
engine, not in an agent. It extracts the numeric claims from narrative text and
matches each against the set of values the deterministic layer actually
produced (the decision, the metrics, the statements). A number that matches
nothing is a candidate hallucination and is flagged.

This is deliberately conservative about *what counts as a claim* — a bare year
or a small count ("5-year", "3 bullets") is not a financial assertion and is
skipped — so that the check flags fabricated figures without drowning in false
positives. The qualitative checks (citations, contradictions, tone) are the
LLM's job in ``app/agents/verifier.py``; this part never calls a model.
"""

from __future__ import annotations

import re
from typing import Any

from app.models.report import NumericIssue

# A number with optional ₹ prefix, thousands separators, decimal, and an
# optional financial suffix (%, x, ×, cr, crore, bn, mn).
_NUMBER = re.compile(
    r"(?P<rupee>₹\s?)?"
    r"(?P<num>\d{1,3}(?:,\d{2,3})+(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s?(?P<suffix>%|x|×|cr\b|crore\b|bn\b|mn\b)?",
    re.IGNORECASE,
)

REL_TOLERANCE = 0.02       # 2% relative
ABS_TOLERANCE = 0.5        # or half a unit, whichever is larger


def _parse(token: str) -> float | None:
    try:
        return float(token.replace(",", ""))
    except ValueError:
        return None


def _is_financial_claim(raw: str, value: float, rupee: bool, suffix: str | None) -> bool:
    """Whether a matched number is a financial assertion worth grounding."""
    if rupee or suffix:
        return True
    if "." in raw:
        return True               # a decimal is almost always a computed figure
    # A bare integer: treat a plausible year or a small count as prose, not a claim.
    if 1900 <= value <= 2100:
        return False
    if value < 100:
        return False
    return True


def extract_financial_numbers(text: str) -> list[tuple[str, float]]:
    """Return ``(raw_token, value)`` for each financial number in ``text``."""
    out: list[tuple[str, float]] = []
    for match in _NUMBER.finditer(text):
        raw = match.group("num")
        value = _parse(raw)
        if value is None:
            continue
        if _is_financial_claim(raw, value, bool(match.group("rupee")), match.group("suffix")):
            out.append((match.group(0).strip(), value))
    return out


def _add(values: set[float], x: Any) -> None:
    if x is None:
        return
    try:
        values.add(round(float(x), 2))
    except (TypeError, ValueError):
        return


def collect_known_values(
    decision: Any,
    metric_set: Any = None,
    statements: Any = None,
    market: Any = None,
) -> set[float]:
    """Every number the deterministic layer produced, for grounding against."""
    values: set[float] = set()
    _add(values, decision.composite_score)
    _add(values, decision.data_completeness_pct)
    _add(values, decision.weight_covered)
    for pillar in decision.pillar_scores:
        _add(values, pillar.score)
        _add(values, pillar.contribution)

    val = decision.valuation
    if val is not None:
        for field in ("current_price", "reconciled_low", "reconciled_high",
                      "fair_value_midpoint", "upside_pct"):
            _add(values, getattr(val, field, None))
        dcf = val.dcf
        for field in ("wacc_pct", "cost_of_equity_pct", "cost_of_debt_pct", "beta_used",
                      "terminal_growth_pct", "fair_value_base", "fair_value_low",
                      "fair_value_high", "enterprise_value", "equity_value", "net_debt"):
            _add(values, getattr(dcf, field, None))
        for cf in (dcf.fcff_forecast or []):
            _add(values, cf)
        if dcf.tax_rate is not None:
            _add(values, dcf.tax_rate * 100.0)   # prose usually states tax as a percent

    if metric_set is not None:
        for metric in metric_set.metrics.values():
            _add(values, metric.value)
            _add(values, metric.final_score)

    if statements is not None:
        for period in statements.annual:
            for field in ("revenue", "pat", "ebitda", "ebit"):
                _add(values, getattr(period.income, field, None))

    if market is not None:
        _add(values, market.cmp)
        _add(values, market.market_cap)
        _add(values, market.shares_outstanding)

    return values


def _matches(value: float, known: set[float]) -> bool:
    for k in known:
        if abs(value - k) <= max(ABS_TOLERANCE, REL_TOLERANCE * abs(k)):
            return True
    return False


def check_numeric_grounding(
    text: str, known: set[float],
) -> tuple[int, list[NumericIssue]]:
    """Return ``(claims_checked, unsupported)`` for the numbers in ``text``."""
    checked = 0
    unsupported: list[NumericIssue] = []
    seen: set[str] = set()
    for raw, value in extract_financial_numbers(text):
        checked += 1
        if _matches(value, known):
            continue
        if raw in seen:
            continue
        seen.add(raw)
        # Grab a little surrounding context for the report.
        idx = text.find(raw)
        context = text[max(0, idx - 30): idx + len(raw) + 20].replace("\n", " ").strip()
        unsupported.append(NumericIssue(value=raw, context=context))
    return checked, unsupported


def check_disclaimer_present(text: str) -> bool:
    """Whether the mandatory educational-use disclaimer is present (PRD 20.2, C5)."""
    lowered = text.lower()
    return "not investment advice" in lowered and "educational" in lowered
