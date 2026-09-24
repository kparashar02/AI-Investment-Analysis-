"""Relative valuation (app/engine/relative_valuation.py).

Peers and own-multiple history arrive with the Phase 3 agent, so these tests
supply them synthetically to pin the peer-median/percentile/implied-value maths
and the premium-vs-quality decomposition, plus the graceful unavailable path
every pure Phase 2 run takes.
"""

from __future__ import annotations

import pytest

from app.engine.relative_valuation import compute_relative_valuation


def test_unavailable_without_peers_or_history(acme):
    result = compute_relative_valuation(acme.statements, acme.market)
    assert result.available is False
    assert result.valid_peer_count == 0
    assert any("peer" in n.lower() for n in result.notes)


def test_peer_median_and_premium(acme):
    # acme P/E = 150 / 14.25 = 10.53. Peers cheaper (median 8) -> acme at a premium.
    result = compute_relative_valuation(
        acme.statements, acme.market,
        peer_multiples={"pe": [6.0, 8.0, 10.0]},
    )
    assert result.available
    assert result.valid_peer_count == 3
    pe = next(c for c in result.peer_comparisons if c.multiple == "pe")
    assert pe.benchmark_value == pytest.approx(8.0)          # median of peers
    assert pe.premium_discount_pct > 0                        # acme richer than peers
    # Implied price = peer median P/E (8) x EPS (14.25) = 114.
    assert pe.implied_value_per_share == pytest.approx(114.0, abs=0.5)


def test_own_history_implied_value(acme):
    result = compute_relative_valuation(
        acme.statements, acme.market,
        own_history_multiples={"pe": [9.0, 10.0, 11.0, 12.0, 13.0]},
    )
    assert result.available
    own = next(c for c in result.own_history_comparisons if c.multiple == "pe")
    assert own.benchmark_value == pytest.approx(11.0)         # own median
    assert own.implied_value_per_share == pytest.approx(11.0 * 14.25, abs=0.5)


def test_premium_justified_by_quality(acme):
    # Expensive vs peers (90th pct valuation) but even higher quality (95th) -> justified.
    result = compute_relative_valuation(
        acme.statements, acme.market,
        peer_multiples={"pe": [5.0, 6.0, 7.0]},   # acme far above -> high valuation percentile
        quality_percentile=95.0,
    )
    assert result.valuation_percentile is not None
    assert result.premium_justified is True


def test_premium_not_justified_when_quality_lags(acme):
    result = compute_relative_valuation(
        acme.statements, acme.market,
        peer_multiples={"pe": [5.0, 6.0, 7.0]},
        quality_percentile=20.0,
    )
    assert result.premium_justified is False


def test_few_peers_noted_for_v10(acme):
    result = compute_relative_valuation(
        acme.statements, acme.market, peer_multiples={"pe": [8.0, 9.0]},
    )
    assert result.valid_peer_count == 2
    assert any("V10" in n for n in result.notes)
