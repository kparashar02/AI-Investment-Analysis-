"""News providers and service (app/data/providers/news.py, news_service.py).

All offline: a fake HTTP transport returns canned Alpha Vantage / NewsAPI
payloads, so the mapping, the fallback chain, the cache and the "no news is
fine" behaviour are pinned without a key or the network.
"""

from __future__ import annotations

from app.data.cache import DiskCache
from app.data.news_service import NewsService
from app.data.providers.base import ProviderDataError
from app.data.providers.news import AlphaVantageNews, NewsApiOrg


class _FakeResponse:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeHttp:
    def __init__(self, payload):
        self._payload = payload
        self.calls = []

    def get(self, url, params=None, headers=None):
        self.calls.append({"url": url, "params": params})
        return _FakeResponse(self._payload)


# --- Alpha Vantage ---------------------------------------------------------

_AV_PAYLOAD = {
    "feed": [
        {"title": "Acme wins export order", "summary": "A large deal.", "source": "Reuters",
         "url": "https://r.com/a", "time_published": "20260320T093000",
         "overall_sentiment_score": 0.42},
    ]
}


def test_alpha_vantage_maps_articles():
    av = AlphaVantageNews(api_key="k", http_client=_FakeHttp(_AV_PAYLOAD))
    articles = av.fetch("ACME.NS", lookback_days=60)
    assert len(articles) == 1
    a = articles[0]
    assert a["title"] == "Acme wins export order"
    assert a["source"] == "Reuters"
    assert a["published_date"] == "2026-03-20"
    # The external sentiment must NOT be carried through (PRD 8.6).
    assert "overall_sentiment_score" not in a
    assert "sentiment" not in a


def test_alpha_vantage_converts_indian_ticker_suffix():
    http = _FakeHttp(_AV_PAYLOAD)
    AlphaVantageNews(api_key="k", http_client=http).fetch("TCS.NS")
    assert http.calls[0]["params"]["tickers"] == "TCS.BSE"


def test_alpha_vantage_unavailable_without_key():
    assert AlphaVantageNews(api_key="").available() is False


def test_alpha_vantage_raises_on_rate_limit_payload():
    av = AlphaVantageNews(api_key="k", http_client=_FakeHttp({"Information": "rate limited"}))
    try:
        av.fetch("ACME.NS")
        assert False, "expected ProviderDataError"
    except ProviderDataError:
        pass


# --- NewsAPI ---------------------------------------------------------------

_NEWSAPI_PAYLOAD = {
    "status": "ok",
    "articles": [
        {"title": "Acme profit rises", "description": "Q4 up.",
         "url": "https://n.com/x", "publishedAt": "2026-03-18T06:00:00Z",
         "source": {"name": "Business Standard"}},
    ],
}


def test_newsapi_maps_articles():
    api = NewsApiOrg(api_key="k", http_client=_FakeHttp(_NEWSAPI_PAYLOAD))
    articles = api.fetch("ACME.NS", name="Acme Industries")
    assert articles[0]["source"] == "Business Standard"
    assert articles[0]["published_date"] == "2026-03-18"


def test_newsapi_queries_by_company_name():
    http = _FakeHttp(_NEWSAPI_PAYLOAD)
    NewsApiOrg(api_key="k", http_client=http).fetch("ACME.NS", name="Acme Industries")
    assert http.calls[0]["params"]["q"] == "Acme Industries"


# --- service: fallback + cache --------------------------------------------

class _FakeProvider:
    def __init__(self, name, articles=None, available=True, fail=False):
        self.name = name
        self._articles = articles or []
        self._available = available
        self._fail = fail
        self.calls = 0

    def available(self):
        return self._available

    def fetch(self, ticker, *, name=None, lookback_days=60):
        self.calls += 1
        if self._fail:
            raise ProviderDataError("boom")
        return self._articles


def _service(providers, tmp_path):
    return NewsService(providers, cache=DiskCache(root=tmp_path))


def test_service_uses_first_provider_with_articles(tmp_path):
    primary = _FakeProvider("av", articles=[{"title": "a"}])
    fallback = _FakeProvider("newsapi", articles=[{"title": "b"}])
    articles = _service([primary, fallback], tmp_path).fetch("ACME.NS")
    assert articles == [{"title": "a"}]
    assert fallback.calls == 0


def test_service_falls_through_on_error(tmp_path):
    primary = _FakeProvider("av", fail=True)
    fallback = _FakeProvider("newsapi", articles=[{"title": "b"}])
    articles = _service([primary, fallback], tmp_path).fetch("ACME.NS")
    assert articles == [{"title": "b"}]


def test_service_skips_unavailable_provider(tmp_path):
    primary = _FakeProvider("av", available=False)
    fallback = _FakeProvider("newsapi", articles=[{"title": "b"}])
    _service([primary, fallback], tmp_path).fetch("ACME.NS")
    assert primary.calls == 0


def test_service_caches_results(tmp_path):
    primary = _FakeProvider("av", articles=[{"title": "a"}])
    service = _service([primary], tmp_path)
    service.fetch("ACME.NS")
    service.fetch("ACME.NS")
    assert primary.calls == 1     # second call served from cache


def test_service_returns_empty_when_no_coverage(tmp_path):
    empty = _FakeProvider("av", articles=[])
    down = _FakeProvider("newsapi", available=False)
    assert _service([empty, down], tmp_path).fetch("ACME.NS") == []


def test_news_source_adapter_uses_identity(tmp_path):
    from app.models.agent_io import CompanyIdentity

    provider = _FakeProvider("av", articles=[{"title": "a"}])
    service = _service([provider], tmp_path)
    identity = CompanyIdentity(ticker="ACME.NS", legal_name="Acme Industries")
    assert service.news_source(identity) == [{"title": "a"}]
