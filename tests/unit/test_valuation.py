"""Valuation reconciliation and the valuation pillar (app/engine/valuation.py)."""

from __future__ import annotations

import pytest

from app.engine.valuation import build_valuation, valuation_pillar_metrics


def test_reconciliation_dcf_only(acme):
    val = build_valuation(acme.statements, acme.market)
    assert val.dcf.available
    # With no peers/history the reconciled range equals the DCF range.
    assert val.reconciled_low == pytest.approx(val.dcf.fair_value_low)
    assert val.reconciled_high == pytest.approx(val.dcf.fair_value_high)
    included = [m for m in val.methods if m.included]
    assert [m.method for m in included] == ["dcf"]
    assert included[0].weight == pytest.approx(1.0)   # renormalised to the only method


def test_upside_computed_against_cmp(acme):
    val = build_valuation(acme.statements, acme.market)
    midpoint = (val.reconciled_low + val.reconciled_high) / 2
    expected = (midpoint / acme.market.cmp - 1.0) * 100.0
    assert val.upside_pct == pytest.approx(expected, abs=0.1)
    assert val.upside_pct > 0    # acme trades below its DCF fair value


def test_reconciliation_blends_dcf_and_peers(acme):
    val = build_valuation(
        acme.statements, acme.market,
        peer_multiples={"pe": [8.0, 9.0, 10.0], "ev_ebitda": [7.0, 8.0, 9.0]},
    )
    methods = {m.method: m for m in val.methods if m.included}
    assert "dcf" in methods and "peer_relative" in methods
    # DCF 0.50 and peer 0.30 renormalise to 0.625 / 0.375.
    assert methods["dcf"].weight == pytest.approx(0.625, abs=0.001)
    assert methods["peer_relative"].weight == pytest.approx(0.375, abs=0.001)


def test_low_confidence_dcf_weight_is_halved(acme):
    from app.models.statements import MarketData

    wild = MarketData(ticker="ACME.NS", cmp=150.0, shares_outstanding=100.0, beta=3.5)
    val = build_valuation(
        acme.statements, wild,
        peer_multiples={"pe": [8.0, 9.0, 10.0]},
    )
    assert val.dcf.confidence == "LOW_CONFIDENCE"
    methods = {m.method: m for m in val.methods if m.included}
    # DCF weight 0.50 halved to 0.25; peer 0.30 -> normalised 0.25/(0.25+0.30) etc.
    total = 0.25 + 0.30
    assert methods["dcf"].weight == pytest.approx(0.25 / total, abs=0.001)


def test_pillar_metrics_upside_available_others_not(acme):
    val = build_valuation(acme.statements, acme.market)
    metrics = valuation_pillar_metrics(val)
    assert metrics["upside_to_fair_value"].value is not None
    # No peers/history -> the three comparison metrics are unavailable.
    assert metrics["pe_vs_peer_median"].value is None
    assert metrics["ev_ebitda_vs_peer_median"].value is None
    assert metrics["pe_vs_own_history"].value is None


def test_pillar_metrics_peers_available(acme):
    val = build_valuation(
        acme.statements, acme.market,
        peer_multiples={"pe": [8.0, 9.0, 10.0], "ev_ebitda": [7.0, 8.0, 9.0]},
    )
    metrics = valuation_pillar_metrics(val)
    assert metrics["pe_vs_peer_median"].value is not None
    assert metrics["ev_ebitda_vs_peer_median"].value is not None


def test_valuation_unavailable_without_market(acme):
    val = build_valuation(acme.statements, None)
    assert not val.dcf.available
    assert val.reconciled_low is None
    assert val.upside_pct is None
