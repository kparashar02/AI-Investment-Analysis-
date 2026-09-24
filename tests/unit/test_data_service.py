"""The data service: fallback chain, cache short-circuit, abort (app/data/service.py).

Every provider here is a fake returning a canned raw payload, so the whole
orchestration is tested offline: no yfinance, no network. The cache is pointed
at pytest's tmp_path.
"""

from __future__ import annotations

import pytest

from app.data.cache import DiskCache
from app.data.providers.base import ProviderDataError, ProviderUnavailable
from app.data.service import DataService, DataUnavailable


def _raw_statements(source: str = "fake") -> dict:
    period = {
        "period_end": "2026-03-31",
        "income": {"Total Revenue": 1.0e12, "Net Income": 1.0e11,
                   "EBITDA": 2.0e11, "Operating Income": 1.5e11,
                   "Reconciled Depreciation": 5.0e10, "Interest Expense": 1.0e10,
                   "Pretax Income": 1.4e11, "Tax Provision": 4.0e10,
                   "Diluted Average Shares": 1.0e10},
        "balance": {"Total Assets": 8.0e11, "Current Assets": 3.0e11,
                    "Current Liabilities": 2.0e11, "Stockholders Equity": 5.0e11,
                    "Total Liabilities Net Minority Interest": 3.0e11,
                    "Current Debt": 2.0e10, "Long Term Debt": 1.0e11,
                    "Cash And Cash Equivalents": 1.0e11, "Inventory": 5.0e10,
                    "Receivables": 6.0e10, "Net PPE": 4.0e11},
        "cashflow": {"Operating Cash Flow": 1.5e11, "Capital Expenditure": -4.0e10,
                     "Free Cash Flow": 1.1e11},
    }
    return {
        "source": source,
        "symbol": "TEST.NS",
        "currency": "INR",
        "profile": {"long_name": "Test Ltd", "sector": "IT Services"},
        "annual": [period, {**period, "period_end": "2025-03-31"},
                   {**period, "period_end": "2024-03-31"}],
        "quarterly": [],
    }


def _raw_market(source: str = "fake") -> dict:
    return {
        "source": source,
        "symbol": "TEST.NS",
        "currency": "INR",
        "quote": {"current_price": 1500.0, "market_cap": 1.5e13,
                  "shares_outstanding": 1.0e10},
        "price_history": [],
    }


class FakeProvider:
    """A provider with configurable behaviour and a call counter."""

    def __init__(self, name="fake", *, available=True, fail=None, payload=None,
                 market=None):
        self.name = name
        self._available = available
        self._fail = fail            # an exception instance to raise, or None
        self._payload = payload if payload is not None else _raw_statements(name)
        self._market = market if market is not None else _raw_market(name)
        self.statement_calls = 0
        self.market_calls = 0

    def available(self) -> bool:
        return self._available

    def fetch_statements(self, ticker: str) -> dict:
        self.statement_calls += 1
        if self._fail is not None:
            raise self._fail
        return dict(self._payload)

    def fetch_market(self, ticker: str) -> dict:
        self.market_calls += 1
        if self._fail is not None:
            raise self._fail
        return dict(self._market)


def _service(providers, tmp_path, use_cache=True):
    return DataService(providers, cache=DiskCache(root=tmp_path, enabled=use_cache))


# --- happy path ------------------------------------------------------------

def test_get_statements_returns_normalised_with_data_quality(tmp_path):
    provider = FakeProvider()
    service = _service([provider], tmp_path)
    stmts = service.get_statements("TEST.NS")
    assert stmts.ticker == "TEST.NS"
    assert stmts.latest.income.revenue == pytest.approx(100_000.0)
    assert stmts.data_quality is not None
    assert stmts.data_quality.sources_used == ["fake"]
    assert stmts.data_quality.annual_periods_available == 3


def test_get_market_normalised(tmp_path):
    service = _service([FakeProvider()], tmp_path)
    market = service.get_market("TEST.NS")
    assert market.cmp == pytest.approx(1500.0)
    assert market.market_cap == pytest.approx(1_500_000.0)


# --- caching ---------------------------------------------------------------

def test_second_call_is_served_from_cache(tmp_path):
    provider = FakeProvider()
    service = _service([provider], tmp_path)
    service.get_statements("TEST.NS")
    service.get_statements("TEST.NS")
    assert provider.statement_calls == 1  # second call hit the cache


def test_cache_served_result_is_disclosed_in_warnings(tmp_path):
    provider = FakeProvider()
    service = _service([provider], tmp_path)
    service.get_statements("TEST.NS")
    stmts = service.get_statements("TEST.NS")
    assert any("cache" in w for w in stmts.data_quality.warnings)


def test_disabled_cache_refetches_every_time(tmp_path):
    provider = FakeProvider()
    service = _service([provider], tmp_path, use_cache=False)
    service.get_statements("TEST.NS")
    service.get_statements("TEST.NS")
    assert provider.statement_calls == 2


# --- fallback chain --------------------------------------------------------

def test_falls_through_to_next_provider_on_error(tmp_path):
    primary = FakeProvider("primary", fail=ProviderDataError("no Indian coverage"))
    fallback = FakeProvider("fallback")
    service = _service([primary, fallback], tmp_path)
    stmts = service.get_statements("TEST.NS")
    assert stmts.data_quality.sources_used == ["fallback"]
    assert primary.statement_calls == 1
    assert fallback.statement_calls == 1


def test_unavailable_provider_is_skipped_without_calling_it(tmp_path):
    primary = FakeProvider("primary", available=False)
    fallback = FakeProvider("fallback")
    service = _service([primary, fallback], tmp_path)
    stmts = service.get_statements("TEST.NS")
    assert stmts.data_quality.sources_used == ["fallback"]
    assert primary.statement_calls == 0  # never called — reported unavailable


def test_all_providers_failing_raises_data_unavailable(tmp_path):
    primary = FakeProvider("primary", fail=ProviderDataError("empty"))
    fallback = FakeProvider("fallback", available=False)
    service = _service([primary, fallback], tmp_path)
    with pytest.raises(DataUnavailable) as excinfo:
        service.get_statements("TEST.NS")
    # The error names every source and why it failed.
    assert "primary" in excinfo.value.attempts
    assert "fallback" in excinfo.value.attempts


def test_provider_bug_does_not_crash_the_chain(tmp_path):
    # An unexpected (non-ProviderError) exception is caught and the chain
    # continues, rather than taking down the whole analysis.
    boom = FakeProvider("boom", fail=KeyError("bug"))
    fallback = FakeProvider("fallback")
    service = _service([boom, fallback], tmp_path)
    stmts = service.get_statements("TEST.NS")
    assert stmts.data_quality.sources_used == ["fallback"]


def test_get_market_or_none_swallows_total_failure(tmp_path):
    provider = FakeProvider("p", fail=ProviderUnavailable("no key"))
    service = _service([provider], tmp_path)
    assert service.get_market_or_none("TEST.NS") is None


def test_non_inr_currency_is_flagged(tmp_path):
    payload = _raw_statements()
    payload["currency"] = "USD"
    service = _service([FakeProvider(payload=payload)], tmp_path)
    stmts = service.get_statements("TEST.NS")
    assert any("USD" in w for w in stmts.data_quality.warnings)
