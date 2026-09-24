"""Self-contained HTML renderer for the report (PRD 13.1, 13.3, 13.4).

Produces one standalone HTML file — inline CSS, inline SVG charts, no external
assets or JavaScript — so it opens anywhere, prints cleanly to PDF from a
browser, and feeds a PDF engine unchanged. Like the Markdown renderer it invents
no numbers: every figure comes from the fixed decision, and the charts are drawn
from the same computed series.

Chart data (the 5-year series, price history, news points) lives on the metric
set, the market data and the news analysis rather than on the report, so those
are passed in as optional extras; when absent, each chart degrades to a labelled
placeholder and the prose still renders.
"""

from __future__ import annotations

import html
from typing import Any

from app.models.decision import InvestmentDecision, Rating
from app.models.report import ResearchReport
from app.report import charts

_RATING_LABEL = {Rating.NO_RATING: "NO RATING", Rating.NOT_SUPPORTED: "NOT SUPPORTED IN V1"}

_CSS = """
:root { --ink:#1e293b; --muted:#64748b; --line:#e2e8f0; --accent:#2563eb; --bg:#ffffff; }
* { box-sizing:border-box; }
body { font:14px/1.55 -apple-system,Segoe UI,Roboto,Helvetica,Arial,sans-serif; color:var(--ink);
       background:#f1f5f9; margin:0; }
.page { max-width:900px; margin:0 auto; background:var(--bg); padding:40px 48px; }
h1 { font-size:26px; margin:0 0 4px; }
h2 { font-size:17px; margin:30px 0 10px; padding-bottom:5px; border-bottom:2px solid var(--line); }
.sub { color:var(--muted); }
.rating { font-size:20px; font-weight:700; color:var(--accent); }
.banner { background:#fff7ed; border:1px solid #fdba74; border-radius:8px; padding:12px 16px; margin:14px 0; }
.banner.warn { background:#fef2f2; border-color:#fca5a5; }
table { border-collapse:collapse; width:100%; margin:10px 0; font-size:13px; }
th,td { border:1px solid var(--line); padding:6px 9px; text-align:left; }
th { background:#f8fafc; }
td.num,th.num { text-align:right; }
ul { margin:6px 0 6px 18px; padding:0; }
.chart { width:100%; height:auto; max-width:640px; display:block; margin:10px auto; border:1px solid var(--line); border-radius:6px; background:#fff; }
.caveat { border-left:3px solid var(--accent); padding:6px 12px; background:#f8fafc; margin:10px 0; }
.disclaimer { font-size:12px; color:var(--muted); white-space:pre-wrap; border:1px solid var(--line);
              border-radius:8px; padding:14px; background:#f8fafc; }
.footer { color:var(--muted); font-size:12px; margin-top:8px; }
@media print { body{background:#fff;} .page{max-width:none; padding:0;} h2{break-after:avoid;} .chart{break-inside:avoid;} }
@page { size:A4; margin:16mm; }
"""


def _e(x: Any) -> str:
    return html.escape(str(x))


def _fmt(v: float | None, suffix: str = "", dp: int = 2) -> str:
    return "n/a" if v is None else f"{v:,.{dp}f}{suffix}"


def _ul(items: list[str], empty: str = "<em>None provided.</em>") -> str:
    return "<ul>" + "".join(f"<li>{_e(i)}</li>" for i in items) + "</ul>" if items else empty


def _section_body(report: ResearchReport, number: int) -> str:
    match = next((s for s in report.sections if s.number == number), None)
    return _e(match.body) if match and match.body else "<em>No commentary generated.</em>"


def _series(metric_set: Any, name: str) -> tuple[list[str], list[float | None]]:
    if metric_set is None or name not in metric_set.series:
        return [], []
    s = metric_set.series[name]
    return s.periods, s.values


def _news_points(news: Any) -> list[tuple[str, float, int]]:
    if news is None:
        return []
    pts = []
    for a in news.articles:
        if a.scored:
            pts.append((a.published_date or "", float(a.financial_impact.direction), a.materiality))
    return pts


def render_html(
    report: ResearchReport, *,
    metric_set: Any = None, market: Any = None, news: Any = None,
) -> str:
    d: InvestmentDecision = report.decision
    rating = _RATING_LABEL.get(d.rating, d.rating.value)
    val = d.valuation

    # ---- charts ----
    periods, revenue = _series(metric_set, "revenue")
    _, pat = _series(metric_set, "pat")
    _, ocf = _series(metric_set, "operating_cash_flow")
    margins = {"Gross": _series(metric_set, "gross_margin")[1],
               "EBITDA": _series(metric_set, "ebitda_margin")[1],
               "Net": _series(metric_set, "net_margin")[1]}
    radar = charts.radar_chart([(p.name, p.score) for p in d.pillar_scores])
    rev_pat = charts.grouped_bar_revenue_pat(periods, revenue, pat)
    margin = charts.margin_lines(periods, margins)
    ocf_chart = charts.ocf_vs_pat(periods, ocf, pat)
    peer = charts.peer_multiples_bar(val.relative.peer_comparisons if val and val.relative else [])
    price = charts.price_band(getattr(market, "price_history", None) if market else None,
                              val.reconciled_low if val else None,
                              val.reconciled_high if val else None,
                              val.current_price if val else None)
    heat = charts.sensitivity_heatmap(val.dcf.sensitivity if val else None)
    news_chart = charts.news_scatter(_news_points(news))

    out: list[str] = ["<!DOCTYPE html>", "<html lang='en'><head><meta charset='utf-8'>",
                      "<meta name='viewport' content='width=device-width, initial-scale=1'>",
                      f"<title>{_e(report.company_name)} — Research Report</title>",
                      f"<style>{_CSS}</style></head><body><div class='page'>"]

    # Cover
    out.append(f"<h1>{_e(report.company_name)} <span class='sub'>· {_e(report.ticker)}</span></h1>")
    out.append(f"<p class='rating'>{_e(rating)}</p>")
    out.append(f"<p>Composite {_fmt(d.composite_score, dp=1)} / 100 · Basis {_e(d.basis.value)} · "
               f"Confidence {_e(d.confidence.value)}</p>")
    if val and val.upside_pct is not None:
        out.append(f"<p>CMP {_fmt(val.current_price, '', 1)} · Fair Value {_fmt(val.reconciled_low, '', 1)}–"
                   f"{_fmt(val.reconciled_high, '', 1)} · Upside {val.upside_pct:+.1f}%</p>")
    out.append(f"<p class='footer'>Period {_e(d.period or 'n/a')} · Data as of {_e(report.as_of or 'n/a')} · "
               f"Generated {_e(report.generated_at or 'n/a')}</p>")
    out.append("<div class='banner'>⚠ <strong>Educational / academic output — not investment advice.</strong></div>")
    if report.unverified_banner:
        out.append("<div class='banner warn'>🚩 <strong>UNVERIFIED CLAIMS</strong> — parts of the narrative "
                   "could not be grounded in the data after two attempts. The computed rating and numbers "
                   "are unaffected.</div>")

    # 1 Executive summary
    out.append("<h2>1. Executive Summary</h2>")
    out.append(_ul(report.executive_summary))
    if report.rating_rationale:
        out.append(f"<p>{_e(report.rating_rationale)}</p>")

    # 2 Score decomposition
    out.append("<h2>2. Score Decomposition</h2>")
    out.append(radar)
    rows = "".join(
        f"<tr><td>{_e(p.name)}{' <em>(inverted)</em>' if p.name=='risk' else ''}</td>"
        f"<td class='num'>{'n/a' if p.score is None else f'{p.score:.1f}'}</td>"
        f"<td class='num'>{p.configured_weight:.0%}</td>"
        f"<td class='num'>{'—' if p.contribution is None else f'{p.contribution:.2f}'}</td></tr>"
        for p in d.pillar_scores)
    out.append("<table><tr><th>Pillar</th><th class='num'>Score</th><th class='num'>Weight</th>"
               f"<th class='num'>Contribution</th></tr>{rows}</table>")
    out.append(f"<p><strong>Composite {_fmt(d.composite_score, dp=2)} / 100</strong> "
               f"(weight covered {d.weight_covered:.0%})</p>")
    fired = [v for v in d.vetoes if v.triggered]
    if fired:
        out.append("<p><strong>Vetoes triggered:</strong></p>" +
                   _ul([f"{v.code} [{v.action}]: {v.description}" for v in fired]))

    # 3 Company overview
    out.append(f"<h2>3. Company Overview</h2><p>Sector: {_e(d.sector_key)}. "
               f"{_e(report.company_name)} ({_e(report.ticker)}).</p>")

    # 4 Financial performance
    out.append("<h2>4. Financial Performance</h2>")
    out.append(f"<p>{_section_body(report, 4)}</p>{rev_pat}{margin}")

    # 5 Ratio analysis
    out.append("<h2>5. Ratio Analysis</h2><p>Full ratio sheet with formulas: "
               "<code>python -m app.cli sheet --working</code>.</p>")

    # 6 Cash flow
    out.append(f"<h2>6. Cash Flow &amp; Earnings Quality</h2><p>{_section_body(report, 6)}</p>{ocf_chart}")

    # 7 Growth
    out.append(f"<h2>7. Growth Analysis</h2><p>{_section_body(report, 7)}</p>")

    # 8 Peer comparison
    out.append("<h2>8. Peer Comparison</h2>")
    rel = val.relative if val else None
    if rel and rel.valid_peer_count:
        out.append(f"<p>{rel.valid_peer_count} peers compared.</p>{peer}")
    else:
        out.append("<p><em>No peer set available.</em></p>")

    # 9 Valuation
    out.append(f"<h2>9. Valuation</h2><p>{_section_body(report, 9)}</p>")
    if val and val.dcf.available:
        dcf = val.dcf
        out.append(
            "<table>"
            f"<tr><th>WACC</th><td class='num'>{_fmt(dcf.wacc_pct,'%')}</td>"
            f"<th>Terminal g</th><td class='num'>{_fmt(dcf.terminal_growth_pct,'%')}</td></tr>"
            f"<tr><th>DCF fair value</th><td class='num'>{_fmt(dcf.fair_value_low,'',1)}–{_fmt(dcf.fair_value_high,'',1)}</td>"
            f"<th>Reconciled</th><td class='num'>{_fmt(val.reconciled_low,'',1)}–{_fmt(val.reconciled_high,'',1)}</td></tr>"
            "</table>")
        out.append(heat + price)
    else:
        out.append("<p><em>Valuation could not be computed.</em></p>")

    # 10 Industry, 11 News, 12 Risk
    out.append(f"<h2>10. Industry &amp; Macro Context</h2><p>{_section_body(report, 10)}</p>")
    out.append(f"<h2>11. News &amp; Event Analysis</h2><p>{_section_body(report, 11)}</p>{news_chart}")
    out.append(f"<h2>12. Risk Assessment</h2><p>{_section_body(report, 12)}</p>")

    # 13 Investment view
    iv = report.investment_view
    out.append("<h2>13. Investment View</h2>")
    out.append("<p><strong>What supports the rating</strong></p>" + _ul(iv.supports_rating))
    out.append("<p><strong>What argues against it</strong></p>" + _ul(iv.argues_against))
    if iv.bear_case:
        out.append(f"<p><strong>Bear case.</strong> {_e(iv.bear_case)}</p>")
    if report.analyst_caveat:
        out.append(f"<div class='caveat'><strong>Analyst caveat.</strong> {_e(report.analyst_caveat)}</div>")

    # 14 What would change
    w = report.what_would_change_our_view
    out.append("<h2>14. What Would Change Our View</h2>")
    out.append("<p><strong>Upgrade if:</strong></p>" + _ul(w.upgrade_triggers))
    out.append("<p><strong>Downgrade if:</strong></p>" + _ul(w.downgrade_triggers))
    out.append(f"<p><strong>Monitorable metrics:</strong> {_e(', '.join(w.monitorable_metrics) or '—')}</p>")

    # 15 Data quality
    out.append("<h2>15. Data Quality &amp; Limitations</h2><ul>"
               f"<li>Data completeness: {_fmt(d.data_completeness_pct,'%',0)}</li>"
               f"<li>Pillars scored: {_e(', '.join(d.pillars_scored) or 'none')}</li>"
               f"<li>Pillars missing: {_e(', '.join(d.pillars_missing) or 'none')}</li>"
               f"<li>Confidence: {_e(d.confidence.value)}</li>")
    if report.verification is not None:
        v = report.verification
        out.append(f"<li>Verification: {'PASSED' if v.passed else 'FAILED'} "
                   f"({v.numeric_claims_checked} numeric claims checked, {len(v.unsupported_numbers)} ungrounded, "
                   f"{v.attempts} attempt(s))</li>")
    out.append("</ul>")

    # 16 Methodology
    out.append(f"<h2>16. Methodology Appendix</h2><p>Config versions — weights {_e(d.weights_version)}, "
               f"thresholds {_e(d.thresholds_version)}, valuation {_e(d.valuation_version)}. The rating is a "
               "deterministic function of the computed pillar scores and the veto rules.</p>")

    # 17 Citations
    out.append("<h2>17. Citations</h2>")
    if report.citations:
        out.append("<ol>" + "".join(f"<li>{_e(c)}</li>" for c in report.citations) + "</ol>")
    else:
        out.append("<p><em>No external citations in this report.</em></p>")

    # 18 Disclaimer
    out.append(f"<h2>18. Disclaimers</h2><div class='disclaimer'>{_e(report.disclaimer)}</div>")

    out.append("</div></body></html>")
    return "\n".join(out)
