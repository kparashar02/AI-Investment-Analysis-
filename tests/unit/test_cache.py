"""Disk cache with per-source TTL (app/data/cache.py).

The cache is what makes the system affordable on metered APIs and a demo
reproducible, so its freshness logic is pinned here with an injected clock —
no real time passes, and no real network is involved.
"""

from __future__ import annotations

from app.data.cache import DEFAULT_TTLS, DiskCache


class FakeClock:
    def __init__(self, t: float = 1000.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


def test_roundtrip_hit(tmp_path):
    cache = DiskCache(root=tmp_path)
    cache.set("yfinance", "statements", "TCS.NS", {"a": 1, "b": [2, 3]})
    assert cache.get("yfinance", "statements", "TCS.NS") == {"a": 1, "b": [2, 3]}


def test_miss_returns_none(tmp_path):
    cache = DiskCache(root=tmp_path)
    assert cache.get("yfinance", "statements", "NOPE.NS") is None


def test_entry_expires_after_ttl(tmp_path):
    clock = FakeClock()
    cache = DiskCache(root=tmp_path, ttls={"quote": 900}, clock=clock)
    cache.set("yfinance", "quote", "TCS.NS", {"price": 100})

    clock.advance(899)
    assert cache.get("yfinance", "quote", "TCS.NS") == {"price": 100}  # still fresh

    clock.advance(2)  # now 901s old, past the 900s TTL
    assert cache.get("yfinance", "quote", "TCS.NS") is None


def test_ttl_is_taken_from_kind(tmp_path):
    cache = DiskCache(root=tmp_path)
    assert cache.ttl_for("annual_statements") == DEFAULT_TTLS["annual_statements"]
    assert cache.ttl_for("quote") == DEFAULT_TTLS["quote"]
    # An unknown kind gets the conservative default, not a month.
    assert cache.ttl_for("something_new") < DEFAULT_TTLS["annual_statements"]


def test_ttl_frozen_at_write_time(tmp_path):
    # Shortening a TTL must not resurrect an entry that was already expired
    # under the TTL it was written with — freshness is decided from the file.
    clock = FakeClock()
    cache = DiskCache(root=tmp_path, ttls={"quote": 900}, clock=clock)
    cache.set("yfinance", "quote", "TCS.NS", {"price": 1})
    clock.advance(1000)  # expired under the 900s TTL it was stored with

    longer = DiskCache(root=tmp_path, ttls={"quote": 100000}, clock=clock)
    assert longer.get("yfinance", "quote", "TCS.NS") is None


def test_disabled_cache_never_stores_or_serves(tmp_path):
    cache = DiskCache(root=tmp_path, enabled=False)
    cache.set("yfinance", "statements", "TCS.NS", {"a": 1})
    assert cache.get("yfinance", "statements", "TCS.NS") is None


def test_corrupt_entry_is_treated_as_miss(tmp_path):
    cache = DiskCache(root=tmp_path)
    cache.set("yfinance", "statements", "TCS.NS", {"a": 1})
    # Corrupt the file on disk.
    path = cache._path("yfinance", "statements", "TCS.NS")
    path.write_text("not json {{{", encoding="utf-8")
    assert cache.get("yfinance", "statements", "TCS.NS") is None


def test_age_seconds_ignores_freshness(tmp_path):
    clock = FakeClock()
    cache = DiskCache(root=tmp_path, ttls={"quote": 900}, clock=clock)
    cache.set("yfinance", "quote", "TCS.NS", {"price": 1})
    clock.advance(5000)  # well past TTL
    assert cache.get("yfinance", "quote", "TCS.NS") is None      # not fresh
    assert cache.age_seconds("yfinance", "quote", "TCS.NS") == 5000  # but still datable


def test_age_seconds_none_when_absent(tmp_path):
    cache = DiskCache(root=tmp_path)
    assert cache.age_seconds("yfinance", "quote", "GONE.NS") is None


def test_ticker_with_awkward_characters_is_safe_on_disk(tmp_path):
    cache = DiskCache(root=tmp_path)
    cache.set("yfinance", "statements", "BRK/B..\\x", {"ok": True})
    assert cache.get("yfinance", "statements", "BRK/B..\\x") == {"ok": True}
