"""Guardrail & veto rules (PRD 12.6).

Vetoes are applied **after** the composite is computed. They exist because a
weighted average can wash out a fact that should be decisive on its own — a
company can score well on growth and still be uninvestable because its Altman Z
is in the distress zone. So certain conditions cap or override the rating
regardless of the number.

Every rule is evaluated here and recorded — the ones that fired *and* the ones
that were checked and did not — with the exact values that decided it, because
the audit trail is the product (PRD 12.6). Rules that need a Phase 3/4 input we
do not yet have (a governance flag, a verification result) are reported as
``evaluable=False`` rather than silently skipped, so the report can be honest
about which guardrails are actually live.

This module reasons about workflow and thresholds, never about finance beyond
the metrics handed to it — and it holds no LLM call, like everything in
``app/engine``.
"""

from __future__ import annotations

from typing import Any

from app.engine.normalisation import is_bfsi
from app.models.decision import Rating, VetoTrigger
from app.models.metrics import MetricSet
from app.models.statements import FinancialStatements

# Veto thresholds. These mirror weights.yaml's `vetoes` conditions; kept as
# named constants so the arithmetic is auditable next to the rule it serves.
NET_DEBT_EBITDA_LIMIT = 5.0
INTEREST_COVER_FLOOR = 1.5
ALTMAN_DISTRESS = 1.81
DOWNSIDE_LIMIT_PCT = 40.0
MIN_PEERS = 3


def evaluate_vetoes(
    metric_set: MetricSet,
    statements: FinancialStatements,
    *,
    data_completeness_pct: float | None,
    upside_pct: float | None,
    valid_peer_count: int = 0,
    governance_severity: str | None = None,
) -> list[VetoTrigger]:
    """Evaluate every guardrail and return the full list, fired or not."""
    vetoes: list[VetoTrigger] = []
    latest = statements.latest

    # V1 — data completeness.
    if data_completeness_pct is None:
        vetoes.append(VetoTrigger(
            code="V1", description="Data completeness below the minimum required to rate",
            action="NO_RATING", evaluable=False, triggered=False,
            note="no data-completeness figure was supplied"))
    else:
        vetoes.append(VetoTrigger(
            code="V1", description="Data completeness below the minimum required to rate",
            action="NO_RATING", evaluable=True,
            triggered=data_completeness_pct < 70.0,
            values={"data_completeness_pct": round(data_completeness_pct, 1)}))

    # V2 — severe leverage with inadequate cover.
    nde = metric_set.value("net_debt_to_ebitda")
    icov = metric_set.value("interest_coverage")
    if nde is None or icov is None:
        vetoes.append(VetoTrigger(
            code="V2", description="Severe leverage with inadequate interest cover",
            action="CAP_AT_REDUCE", evaluable=False, triggered=False,
            note="net_debt_to_ebitda or interest_coverage unavailable"))
    else:
        vetoes.append(VetoTrigger(
            code="V2", description="Severe leverage with inadequate interest cover",
            action="CAP_AT_REDUCE", evaluable=True,
            triggered=nde > NET_DEBT_EBITDA_LIMIT and icov < INTEREST_COVER_FLOOR,
            values={"net_debt_to_ebitda": round(nde, 2), "interest_coverage": round(icov, 2)}))

    # V3 — persistently poor cash conversion.
    streak = metric_set.screens.get("cash_conversion_streak")
    if streak is None or streak.score is None:
        vetoes.append(VetoTrigger(
            code="V3", description="Poor cash conversion (OCF/PAT < 0.5) for 3 consecutive years",
            action="CAP_AT_HOLD", evaluable=False, triggered=False,
            note="cash-conversion streak unavailable"))
    else:
        vetoes.append(VetoTrigger(
            code="V3", description="Poor cash conversion (OCF/PAT < 0.5) for 3 consecutive years",
            action="CAP_AT_HOLD", evaluable=True,
            triggered=streak.score >= 3.0,
            values={"consecutive_poor_years": streak.score}))

    # V4 — Altman Z in the distress zone.
    altman = metric_set.value("altman_z_score")
    if altman is None:
        vetoes.append(VetoTrigger(
            code="V4", description="Altman Z-Score in the distress zone (< 1.81)",
            action="CAP_AT_REDUCE", evaluable=False, triggered=False,
            note="Altman Z-Score unavailable"))
    else:
        vetoes.append(VetoTrigger(
            code="V4", description="Altman Z-Score in the distress zone (< 1.81)",
            action="CAP_AT_REDUCE", evaluable=True,
            triggered=altman < ALTMAN_DISTRESS,
            values={"altman_z_score": round(altman, 2)}))

    # V5 — negative equity.
    equity = latest.balance.shareholders_equity
    if equity is None:
        vetoes.append(VetoTrigger(
            code="V5", description="Negative shareholders' equity",
            action="NO_RATING", evaluable=False, triggered=False,
            note="shareholders' equity unavailable"))
    else:
        vetoes.append(VetoTrigger(
            code="V5", description="Negative shareholders' equity",
            action="NO_RATING", evaluable=True, triggered=equity < 0,
            values={"shareholders_equity": round(equity, 2)},
            note="negative equity — flagged for analyst review rather than auto-rated"))

    # V6 — high-severity governance flag (supplied by the Phase 3 risk agent).
    if governance_severity is None:
        vetoes.append(VetoTrigger(
            code="V6", description="High-severity, evidenced governance flag",
            action="CAP_AT_HOLD", evaluable=False, triggered=False,
            note="no risk assessment supplied; requires the Phase 3 risk agent"))
    else:
        vetoes.append(VetoTrigger(
            code="V6", description="High-severity, evidenced governance flag",
            action="CAP_AT_HOLD", evaluable=True,
            triggered=str(governance_severity).upper() == "HIGH",
            values={"governance_severity": str(governance_severity).upper()}))

    # V7 — large downside to reconciled fair value.
    if upside_pct is None:
        vetoes.append(VetoTrigger(
            code="V7", description="Large downside to reconciled fair value (> 40%)",
            action="CAP_AT_HOLD", evaluable=False, triggered=False,
            note="no reconciled fair value available"))
    else:
        vetoes.append(VetoTrigger(
            code="V7", description="Large downside to reconciled fair value (> 40%)",
            action="CAP_AT_HOLD", evaluable=True,
            triggered=upside_pct < -DOWNSIDE_LIMIT_PCT,
            values={"downside_pct": round(-upside_pct, 2)}))

    # V8 — BFSI, out of scope in V1.
    bfsi = is_bfsi(statements.sector, statements.industry)
    vetoes.append(VetoTrigger(
        code="V8", description="BFSI sector — FCFF-DCF invalid, not supported in V1",
        action="NOT_SUPPORTED", evaluable=True, triggered=bfsi,
        values={"is_bfsi": bfsi}))

    # V9 — verification failed twice (Phase 4).
    vetoes.append(VetoTrigger(
        code="V9", description="Report failed verification twice",
        action="DOWNGRADE_CONFIDENCE", evaluable=False, triggered=False,
        note="requires the Phase 4 verifier; not evaluated deterministically"))

    # V10 — insufficient peers for relative valuation.
    vetoes.append(VetoTrigger(
        code="V10", description="Fewer than 3 valid peers for relative valuation",
        action="HALVE_VALUATION_WEIGHT", evaluable=True,
        triggered=valid_peer_count < MIN_PEERS,
        values={"valid_peer_count": valid_peer_count},
        note="valuation already redistributes weight when peers are absent" if valid_peer_count < MIN_PEERS else None))

    return vetoes


def apply_vetoes(rating: Rating, vetoes: list[VetoTrigger]) -> tuple[Rating, list[str], bool]:
    """Apply the fired vetoes to a band-derived rating.

    Returns ``(final_rating, applied_codes, confidence_downgraded)``. An
    override (NO_RATING / NOT_SUPPORTED) wins outright; otherwise the caps
    stack and the worst one governs. HALVE_VALUATION_WEIGHT does not touch the
    rating (it already shaped the valuation), so it is not counted as applied.
    """
    applied: list[str] = []
    confidence_downgraded = False
    final = rating

    # Hard overrides first — a rating that must not be issued at all.
    for veto in vetoes:
        if not veto.triggered:
            continue
        if veto.action in ("NO_RATING", "NOT_SUPPORTED"):
            applied.append(veto.code)
            final = Rating.NOT_SUPPORTED if veto.action == "NOT_SUPPORTED" else Rating.NO_RATING
            return final, applied, confidence_downgraded

    # Caps — take the worst.
    for veto in vetoes:
        if not veto.triggered:
            continue
        if veto.action == "CAP_AT_REDUCE":
            final = final.capped_at(Rating.REDUCE)
            applied.append(veto.code)
        elif veto.action == "CAP_AT_HOLD":
            final = final.capped_at(Rating.HOLD)
            applied.append(veto.code)
        elif veto.action == "DOWNGRADE_CONFIDENCE":
            confidence_downgraded = True
            applied.append(veto.code)

    return final, applied, confidence_downgraded
