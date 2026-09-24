"""yfinance provider frame extraction (app/data/providers/yfinance_fallback.py).

The provider's one job is to turn yfinance's pandas frames into the plain raw
payload of the provider contract. That is exercised here with a fake ``Ticker``
whose attributes duck-type just enough of a pandas frame — so the extraction is
tested with no yfinance dependency, no pandas and no network.
"""

from __future__ import annotations

import math

import pytest

from app.data.normalise import normalise_statements
from app.data.providers.base import ProviderDataError
from app.data.providers.yfinance_fallback import YFinanceProvider


class FakeColumn:
    def __init__(self, mapping: dict):
        self._m = mapping

    def items(self):
        return self._m.items()


class FakeFrame:
    """Minimal stand-in for a yfinance statement frame: columns are period-end
    strings, each column maps line-item label -> value."""

    def __init__(self, data: dict[str, dict]):
        self._data = data
        self.columns = list(data.keys())
        self.empty = not data

    def __getitem__(self, col):
        return FakeColumn(self._data[col])


class FakeTicker:
    def __init__(self, symbol: str):
        self.symbol = symbol
        self.income_stmt = FakeFrame({
            "2026-03-31": {"Total Revenue": 2.0e12, "Net Income": 2.7e11,
                           "EBITDA": 5.0e11, "Bogus Line": float("nan")},
            "2025-03-31": {"Total Revenue": 1.8e12, "Net Income": 2.4e11},
        })
        self.balance_sheet = FakeFrame({
            "2026-03-31": {"Total Assets": 1.5e12, "Stockholders Equity": 9.0e11},
            "2025-03-31": {"Total Assets": 1.4e12, "Stockholders Equity": 8.0e11},
        })
        self.cashflow = FakeFrame({
            "2026-03-31": {"Operating Cash Flow": 3.0e11, "Capital Expenditure": -8.0e10},
            "2025-03-31": {"Operating Cash Flow": 2.7e11, "Capital Expenditure": -7.0e10},
        })
        self.quarterly_income_stmt = FakeFrame({})
        self.quarterly_balance_sheet = FakeFrame({})
        self.quarterly_cashflow = FakeFrame({})
        self.info = {
            "longName": "Fake Industries Ltd",
            "sector": "Capital Goods",
            "industry": "Industrial Machinery",
            "financialCurrency": "INR",
            "currentPrice": 3500.0,
            "marketCap": 3.5e13,
            "sharesOutstanding": 1.0e10,
            "beta": 0.9,
            "fiftyTwoWeekHigh": 4000.0,
            "fiftyTwoWeekLow": 3000.0,
            "averageVolume": 1_000_000,
        }

    def history(self, period=None, interval=None):
        return FakeFrame({"Close": {"2026-03-31": 3500.0, "2025-03-31": 3000.0}})


def _provider():
    return YFinanceProvider(ticker_factory=lambda s: FakeTicker(s))


def test_available_true_with_injected_factory():
    assert _provider().available() is True


def test_fetch_statements_builds_raw_payload():
    raw = _provider().fetch_statements("TEST.NS")
    assert raw["source"] == "yfinance"
    assert raw["symbol"] == "TEST.NS"
    assert raw["currency"] == "INR"
    assert raw["profile"]["sector"] == "Capital Goods"
    labels = {p["period_end"] for p in raw["annual"]}
    assert labels == {"2026-03-31", "2025-03-31"}


def test_periods_are_newest_first_and_merged_across_statements():
    raw = _provider().fetch_statements("TEST.NS")
    first = raw["annual"][0]
    assert first["period_end"] == "2026-03-31"
    # All three statements merged onto the same period.
    assert "Total Revenue" in first["income"]
    assert "Total Assets" in first["balance"]
    assert "Operating Cash Flow" in first["cashflow"]


def test_nan_cells_are_dropped():
    raw = _provider().fetch_statements("TEST.NS")
    first = raw["annual"][0]
    assert "Bogus Line" not in first["income"]
    assert all(not math.isnan(v) for v in first["income"].values())


def test_raw_values_are_preserved_unconverted():
    # The provider must NOT convert units or signs — that is normalisation's job.
    raw = _provider().fetch_statements("TEST.NS")
    first = raw["annual"][0]
    assert first["income"]["Total Revenue"] == 2.0e12       # still absolute
    assert first["cashflow"]["Capital Expenditure"] == -8.0e10  # still negative


def test_extraction_feeds_normalisation_end_to_end():
    raw = _provider().fetch_statements("TEST.NS")
    stmts = normalise_statements(raw)
    assert stmts.latest.label == "FY2026"
    assert stmts.latest.income.revenue == pytest.approx(200_000.0)
    assert stmts.latest.cashflow.capex == pytest.approx(8_000.0)  # sign fixed downstream


def test_fetch_market_builds_quote_and_history():
    raw = _provider().fetch_market("TEST.NS")
    assert raw["quote"]["current_price"] == 3500.0
    assert raw["quote"]["market_cap"] == 3.5e13
    assert raw["price_history"][0] == ["2026-03-31", 3500.0]


def test_empty_statements_raise_provider_data_error():
    class EmptyTicker(FakeTicker):
        def __init__(self, symbol):
            super().__init__(symbol)
            self.income_stmt = FakeFrame({})
            self.balance_sheet = FakeFrame({})
            self.cashflow = FakeFrame({})

    provider = YFinanceProvider(ticker_factory=lambda s: EmptyTicker(s))
    with pytest.raises(ProviderDataError):
        provider.fetch_statements("TEST.NS")


def test_raising_info_property_does_not_crash_statements():
    # yfinance's .info hits the network and can raise; the provider must treat
    # that as a miss and still return the statements it did get.
    class RaisingInfoTicker:
        def __init__(self, symbol):
            base = FakeTicker(symbol)
            self.income_stmt = base.income_stmt
            self.balance_sheet = base.balance_sheet
            self.cashflow = base.cashflow
            self.quarterly_income_stmt = base.quarterly_income_stmt
            self.quarterly_balance_sheet = base.quarterly_balance_sheet
            self.quarterly_cashflow = base.quarterly_cashflow

        @property
        def info(self):
            raise RuntimeError("Yahoo rate limited")

    provider = YFinanceProvider(ticker_factory=lambda s: RaisingInfoTicker(s))
    raw = provider.fetch_statements("TEST.NS")
    assert raw["annual"]                         # statements survived
    assert raw["profile"]["sector"] is None      # profile degraded to empty, no crash


def test_missing_price_raises_provider_data_error():
    class NoPriceTicker(FakeTicker):
        def __init__(self, symbol):
            super().__init__(symbol)
            self.info = {k: v for k, v in self.info.items()
                         if k not in ("currentPrice", "regularMarketPrice", "previousClose")}

    provider = YFinanceProvider(ticker_factory=lambda s: NoPriceTicker(s))
    with pytest.raises(ProviderDataError):
        provider.fetch_market("TEST.NS")
