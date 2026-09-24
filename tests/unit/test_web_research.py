"""Allowlisted web research service (app/data/web_research.py) — offline."""

from __future__ import annotations

from app.data.allowlist import SourceAllowlist
from app.data.cache import DiskCache
from app.data.web_research import WebResearchService, format_citation


class _FakeBackend:
    name = "fake"

    def __init__(self, results, available=True):
        self._results = results
        self._available = available
        self.calls = 0

    def available(self):
        return self._available

    def search(self, query, *, count=10):
        self.calls += 1
        return self._results


# A mix: tier-1 allowed, tier-2 allowed, blocked, and un-allowlisted.
_RESULTS = [
    {"url": "https://www.ibef.org/industry/engineering",
     "title": "Engineering sector", "description": "India engineering output is set to grow.", "age": "2026-01-01"},
    {"url": "https://twitter.com/tipster", "title": "hot tip", "description": "buy now!!!"},
    {"url": "https://randomsite.example/post", "title": "random", "description": "unlisted source"},
    {"url": "https://reuters.com/markets/india",
     "title": "India markets", "description": "Capital goods demand is firm on a public capex push this year."},
]


def _service(backend, tmp_path):
    return WebResearchService(allowlist=SourceAllowlist(), backend=backend,
                              cache=DiskCache(root=tmp_path))


def test_only_allowlisted_sources_survive(tmp_path):
    research = _service(_FakeBackend(_RESULTS), tmp_path).research("Capital Goods", "Industrial Machinery")
    domains = {c.source for c in research.citations}
    assert domains == {"ibef.org", "reuters.com"}     # blocked + unlisted dropped
    assert all(c.tier in (1, 2) for c in research.citations)


def test_snippets_are_wrapped_and_attributed(tmp_path):
    research = _service(_FakeBackend(_RESULTS), tmp_path).research("Capital Goods", "Machinery")
    assert all(s.startswith("<source ") and s.endswith("</source>") for s in research.snippets)
    assert any("ibef.org" in s for s in research.snippets)


def test_quotes_are_truncated_to_word_limit(tmp_path):
    long = [{"url": "https://reuters.com/x", "title": "t",
             "description": " ".join(f"word{i}" for i in range(60))}]
    research = _service(_FakeBackend(long), tmp_path).research("s", "i")
    words = research.citations[0].claim.replace("…", "").split()
    assert len(words) <= 25


def test_citations_carry_tier_and_access_date(tmp_path):
    research = _service(_FakeBackend(_RESULTS), tmp_path).research("s", "i", as_of="2026-09-24")
    c = next(c for c in research.citations if c.source == "ibef.org")
    assert c.tier == 2
    assert c.access_date == "2026-09-24"
    assert "ibef.org" in format_citation(c) and "Tier 2" in format_citation(c)


def test_no_backend_key_yields_no_research(tmp_path):
    research = _service(_FakeBackend(_RESULTS, available=False), tmp_path).research("s", "i")
    assert research.snippets == []
    assert research.citations == []


def test_results_are_cached(tmp_path):
    backend = _FakeBackend(_RESULTS)
    service = _service(backend, tmp_path)
    service.research("Capital Goods", "Machinery")
    service.research("Capital Goods", "Machinery")
    assert backend.calls == 1        # second call served from cache


def test_all_results_blocked_returns_empty(tmp_path):
    blocked = [{"url": "https://x.com/a", "description": "noise"},
               {"url": "https://spammultibagger.com/b", "description": "tips"}]
    research = _service(_FakeBackend(blocked), tmp_path).research("s", "i")
    assert research.citations == []
