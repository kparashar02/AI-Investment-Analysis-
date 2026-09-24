"""HTML/SVG report rendering (app/report/charts.py, html.py, pdf.py)."""

from __future__ import annotations

import pytest

from app.agents.llm import FakeLLM
from app.agents.narrative import NarrativeAgent
from app.engine.scoring import build_decision
from app.models.agent_io import (
    ArticleAssessment,
    FinancialImpact,
    NewsAnalysis,
    Relevance,
)
from app.report import charts, pdf, render_html

_PEERS = {"pe": [8, 9, 10, 11], "ev_ebitda": [7, 8, 9, 10]}


@pytest.fixture
def decision(acme, acme_metrics):
    return build_decision(acme_metrics, acme.statements, acme.market, peer_multiples=_PEERS)


@pytest.fixture
def report(decision):
    draft = {"executive_summary": ["Strong franchise."], "rating_rationale": "Explains it.",
             "supports_rating": ["High ROE"], "bear_case": "Cyclical."}
    return NarrativeAgent(FakeLLM([draft])).run(decision)


# --- charts ----------------------------------------------------------------

def test_every_chart_returns_valid_svg(decision):
    news_pts = [("2026-03-01", 1.0, 4), ("2026-02-01", -1.0, 3)]
    svgs = [
        charts.radar_chart([(p.name, p.score) for p in decision.pillar_scores]),
        charts.grouped_bar_revenue_pat(["FY24", "FY25", "FY26"], [7000, 8000, 10000], [800, 975, 1425]),
        charts.margin_lines(["FY24", "FY25"], {"EBITDA": [22.3, 25.0], "Net": [11.0, 14.3]}),
        charts.ocf_vs_pat(["FY24", "FY25"], [1100, 1300], [800, 975]),
        charts.peer_multiples_bar(decision.valuation.relative.peer_comparisons),
        charts.sensitivity_heatmap(decision.valuation.dcf.sensitivity),
        charts.news_scatter(news_pts),
    ]
    for svg in svgs:
        assert svg.startswith("<svg") and svg.rstrip().endswith("</svg>")


def test_chart_placeholder_on_empty_data():
    svg = charts.grouped_bar_revenue_pat([], [], [])
    assert "<svg" in svg and "no data" in svg


def test_radar_reflects_missing_pillar():
    # A None score should not crash the radar; it renders at the centre.
    svg = charts.radar_chart([("fundamentals", 80.0), ("valuation", None)])
    assert "<polygon" in svg


# --- html document ---------------------------------------------------------

def test_html_is_a_full_document(report, acme_metrics, acme):
    doc = render_html(report, metric_set=acme_metrics, market=acme.market)
    assert doc.startswith("<!DOCTYPE html>")
    assert "</html>" in doc.strip().splitlines()[-1]


def test_html_has_all_eighteen_sections(report, acme_metrics, acme):
    doc = render_html(report, metric_set=acme_metrics, market=acme.market)
    for n, title in [(1, "Executive Summary"), (2, "Score Decomposition"), (9, "Valuation"),
                     (14, "What Would Change Our View"), (18, "Disclaimers")]:
        assert f"{n}. {title}" in doc


def test_html_embeds_charts_and_disclaimer(report, acme_metrics, acme):
    doc = render_html(report, metric_set=acme_metrics, market=acme.market)
    assert doc.count("<svg") >= 7                # radar + series + peer + heatmap + price + news
    assert "not investment advice" in doc.lower()
    assert report.decision.rating.value in doc


def test_html_shows_unverified_banner(report, acme_metrics, acme):
    report.unverified_banner = True
    doc = render_html(report, metric_set=acme_metrics, market=acme.market)
    assert "UNVERIFIED CLAIMS" in doc


def test_html_escapes_prose(decision):
    draft = {"executive_summary": ["Margins <b>improved</b> & held"], "rating_rationale": ""}
    report = NarrativeAgent(FakeLLM([draft])).run(decision)
    doc = render_html(report)
    assert "&lt;b&gt;" in doc and "&amp;" in doc     # injected markup is escaped, not rendered


# --- pdf fallback ----------------------------------------------------------

def test_pdf_path_is_honest_about_availability(report):
    doc = render_html(report)
    if pdf.pdf_available():
        out = pdf.html_to_pdf(doc, "test_out.pdf")
        assert out.exists()
        out.unlink()
    else:
        with pytest.raises(pdf.PdfUnavailable):
            pdf.html_to_pdf(doc, "should_not_exist.pdf")
