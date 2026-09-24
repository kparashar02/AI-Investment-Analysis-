"""Markdown renderer for the research report (PRD 13.1).

Assembles the 18-section document from the fixed decision (facts) and the
generated narrative (prose). Every figure printed here comes from the decision
object, so the document cannot show a number the engine did not compute — the
renderer has no way to invent one.
"""

from __future__ import annotations

from app.models.decision import InvestmentDecision, Rating
from app.models.report import ResearchReport

_RATING_LABEL = {
    Rating.NO_RATING: "NO RATING",
    Rating.NOT_SUPPORTED: "NOT SUPPORTED IN V1",
}


def _fmt(value: float | None, suffix: str = "", dp: int = 2) -> str:
    return "n/a" if value is None else f"{value:,.{dp}f}{suffix}"


def _bullets(items: list[str], empty: str = "_None provided._") -> str:
    return "\n".join(f"- {i}" for i in items) if items else empty


def _cover(report: ResearchReport, decision: InvestmentDecision) -> list[str]:
    rating = _RATING_LABEL.get(decision.rating, decision.rating.value)
    val = decision.valuation
    lines = [
        f"# {report.company_name}  ·  {report.ticker}",
        "",
        f"**RECOMMENDATION: {rating}**   ·   Composite Score: "
        f"{_fmt(decision.composite_score, dp=1)} / 100   ·   Basis: {decision.basis.value}   ·   "
        f"Confidence: {decision.confidence.value}",
    ]
    if val is not None and val.upside_pct is not None:
        lines.append(
            f"CMP {_fmt(val.current_price, '', 1)}   ·   Fair Value "
            f"{_fmt(val.reconciled_low, '', 1)}–{_fmt(val.reconciled_high, '', 1)}   ·   "
            f"Upside {val.upside_pct:+.1f}%")
    lines.append(
        f"Period {decision.period or 'n/a'}   ·   Data as of {report.as_of or 'n/a'}   ·   "
        f"Generated {report.generated_at or 'n/a'}")
    lines.append("")
    lines.append("> ⚠ **Educational / academic output — not investment advice.**")
    if report.unverified_banner:
        lines += [
            "",
            "> 🚩 **UNVERIFIED CLAIMS** — parts of the narrative below could not be "
            "grounded in the underlying data after two attempts. Treat the prose with "
            "caution; the computed rating and numbers are unaffected.",
        ]
    return lines


def _score_decomposition(decision: InvestmentDecision) -> list[str]:
    lines = ["## 2. Score Decomposition", "", "| Pillar | Score | Weight | Contribution |",
             "|---|---:|---:|---:|"]
    for pillar in decision.pillar_scores:
        score = "n/a" if pillar.score is None else f"{pillar.score:.1f}"
        weight = f"{pillar.configured_weight:.0%}"
        contrib = "—" if pillar.contribution is None else f"{pillar.contribution:.2f}"
        note = " *(inverted)*" if pillar.name == "risk" else ""
        lines.append(f"| {pillar.name}{note} | {score} | {weight} | {contrib} |")
    lines += ["", f"**Composite: {_fmt(decision.composite_score, dp=2)} / 100** "
              f"(weight covered {decision.weight_covered:.0%}, basis {decision.basis.value})"]
    fired = [v for v in decision.vetoes if v.triggered]
    if fired:
        lines += ["", "**Vetoes triggered:**"]
        lines += [f"- {v.code} [{v.action}]: {v.description}" for v in fired]
    return lines


def _valuation(decision: InvestmentDecision, body: str) -> list[str]:
    lines = ["## 9. Valuation", ""]
    if body:
        lines += [body, ""]
    val = decision.valuation
    if val is None or not val.dcf.available:
        lines.append("_Valuation could not be computed._")
        return lines
    d = val.dcf
    lines += [
        "| DCF build-up | Value |", "|---|---:|",
        f"| WACC | {_fmt(d.wacc_pct, '%')} |",
        f"| Cost of equity | {_fmt(d.cost_of_equity_pct, '%')} |",
        f"| Cost of debt | {_fmt(d.cost_of_debt_pct, '%')} |",
        f"| Terminal growth | {_fmt(d.terminal_growth_pct, '%')} |",
        f"| DCF fair value | {_fmt(d.fair_value_low, '', 1)}–{_fmt(d.fair_value_high, '', 1)} "
        f"(base {_fmt(d.fair_value_base, '', 1)}) [{d.confidence}] |",
        f"| Reconciled fair value | {_fmt(val.reconciled_low, '', 1)}–{_fmt(val.reconciled_high, '', 1)} |",
        f"| Upside to CMP | {_fmt(val.upside_pct, '%', 1)} |",
    ]
    return lines


def _section(report: ResearchReport, number: int, default_title: str) -> list[str]:
    match = next((s for s in report.sections if s.number == number), None)
    title = match.title if match else default_title
    body = match.body if match and match.body else "_No commentary generated._"
    return [f"## {number}. {title}", "", body]


def render_markdown(report: ResearchReport) -> str:
    """Render the full 18-section report as Markdown."""
    decision = report.decision
    out: list[str] = []
    out += _cover(report, decision)

    out += ["", "## 1. Executive Summary", "", _bullets(report.executive_summary)]
    if report.rating_rationale:
        out += ["", report.rating_rationale]

    out += [""] + _score_decomposition(decision)

    out += ["", "## 3. Company Overview", "",
            f"Sector: {decision.sector_key}. {report.company_name} ({report.ticker})."]

    out += [""] + _section(report, 4, "Financial Performance")
    out += ["", "## 5. Ratio Analysis", "",
            "_Full ratio sheet with formulas is available via_ `python -m app.cli sheet --working`_._"]
    out += [""] + _section(report, 6, "Cash Flow & Earnings Quality")
    out += [""] + _section(report, 7, "Growth Analysis")

    # 8. Peer comparison
    out += ["", "## 8. Peer Comparison", ""]
    rel = decision.valuation.relative if decision.valuation else None
    if rel and rel.valid_peer_count:
        out.append(f"{rel.valid_peer_count} peers compared.")
        for c in rel.peer_comparisons:
            out.append(f"- {c.multiple}: company {_fmt(c.company_value)} vs peer median "
                       f"{_fmt(c.benchmark_value)} ({_fmt(c.premium_discount_pct, '%', 1)})")
    else:
        out.append("_No peer set available._")

    out += [""] + _valuation(decision, next((s.body for s in report.sections if s.number == 9), ""))
    out += [""] + _section(report, 10, "Industry & Macro Context")
    out += [""] + _section(report, 11, "News & Event Analysis")
    out += [""] + _section(report, 12, "Risk Assessment")

    # 13. Investment view
    iv = report.investment_view
    out += ["", "## 13. Investment View", "", "**What supports the rating**", _bullets(iv.supports_rating),
            "", "**What argues against it**", _bullets(iv.argues_against)]
    if iv.bear_case:
        out += ["", "**Bear case**", "", iv.bear_case]
    if report.analyst_caveat:
        out += ["", "**Analyst caveat**", "", f"> {report.analyst_caveat}"]

    # 14. What would change our view
    w = report.what_would_change_our_view
    out += ["", "## 14. What Would Change Our View", "", "**Upgrade if:**", _bullets(w.upgrade_triggers),
            "", "**Downgrade if:**", _bullets(w.downgrade_triggers),
            "", "**Monitorable metrics:** " + (", ".join(w.monitorable_metrics) or "—")]

    # 15. Data quality
    out += ["", "## 15. Data Quality & Limitations", "",
            f"- Data completeness: {_fmt(decision.data_completeness_pct, '%', 0)}",
            f"- Pillars scored: {', '.join(decision.pillars_scored) or 'none'}",
            f"- Pillars missing: {', '.join(decision.pillars_missing) or 'none'}",
            f"- Confidence: {decision.confidence.value}"]
    if report.verification is not None:
        v = report.verification
        out.append(f"- Verification: {'PASSED' if v.passed else 'FAILED'} "
                   f"({v.numeric_claims_checked} numeric claims checked, "
                   f"{len(v.unsupported_numbers)} ungrounded, {v.attempts} attempt(s))")

    # 16. Methodology
    out += ["", "## 16. Methodology Appendix", "",
            f"Config versions — weights {decision.weights_version}, "
            f"thresholds {decision.thresholds_version}, valuation {decision.valuation_version}. "
            "Pillar weights, thresholds and veto rules are versioned YAML; the rating is a "
            "deterministic function of the computed scores and the veto rules."]

    # 17. Citations
    out += ["", "## 17. Citations", ""]
    out += ([f"{i}. {c}" for i, c in enumerate(report.citations, 1)] if report.citations
            else ["_No external citations in this report._"])

    # 18. Disclaimers
    out += ["", "## 18. Disclaimers", "", "> " + report.disclaimer.replace("\n", "\n> ")]

    return "\n".join(out) + "\n"
