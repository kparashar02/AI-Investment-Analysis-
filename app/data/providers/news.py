"""News providers (PRD 9.1, 8.6).

Fetch recent raw articles for a company — ``{title, summary, source, url,
published_date}`` — which the News Analysis Agent then assesses one at a time.
Primary source is Alpha Vantage ``NEWS_SENTIMENT``; NewsAPI is the fallback.

One design rule from PRD 8.6 is enforced structurally here: Alpha Vantage's own
``overall_sentiment_score`` is **not mapped into the raw article**. The project's
news score is its agent's reasoning, computed in Python, and the external
sentiment is deliberately given nowhere to leak in.

Like every provider, these import their transport lazily, take an injectable
``http_client`` for offline testing, and report themselves unavailable (rather
than crashing) when their API key is missing. Alpha Vantage's free tier is
~25 requests/day and 5/minute, so a token bucket paces it and the caller caches
aggressively (PRD 9.2).
"""

from __future__ import annotations

import os
from datetime import date, datetime, timedelta
from typing import Any, Protocol, runtime_checkable

from app.data.providers.base import ProviderDataError, ProviderUnavailable
from app.data.rate_limiter import TokenBucket


@runtime_checkable
class NewsProvider(Protocol):
    name: str

    def available(self) -> bool: ...

    def fetch(self, ticker: str, *, name: str | None = None,
              lookback_days: int = 60) -> list[dict[str, Any]]: ...


def _lazy_httpx(timeout: float) -> Any:
    try:
        import httpx  # noqa: PLC0415
    except ImportError as exc:  # pragma: no cover
        raise ProviderUnavailable("httpx is not installed; needed for news retrieval") from exc
    return httpx.Client(timeout=timeout)


class AlphaVantageNews:
    """Alpha Vantage NEWS_SENTIMENT (PRD 9.1 primary)."""

    name = "alpha_vantage"
    BASE = "https://www.alphavantage.co/query"

    def __init__(self, *, api_key: str | None = None, http_client: Any = None,
                 bucket: TokenBucket | None = None, timeout: float = 30.0) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("ALPHA_VANTAGE_API_KEY", "")
        self._http = http_client
        # ~5 requests/minute on the free tier.
        self._bucket = bucket or TokenBucket(capacity=5, refill_rate=5 / 60)
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def _client(self) -> Any:
        if self._http is None:
            self._http = _lazy_httpx(self.timeout)
        return self._http

    @staticmethod
    def _av_ticker(ticker: str) -> str:
        # Alpha Vantage uses an exchange suffix like '.BSE' for Indian listings.
        return ticker.replace(".NS", ".BSE").replace(".BO", ".BSE")

    @staticmethod
    def _av_date(stamp: str | None) -> str | None:
        if not stamp:
            return None
        try:
            return datetime.strptime(stamp[:15], "%Y%m%dT%H%M%S").date().isoformat()
        except ValueError:
            return stamp[:10]

    def fetch(self, ticker: str, *, name: str | None = None,
              lookback_days: int = 60) -> list[dict[str, Any]]:
        if not self.available():
            raise ProviderUnavailable("ALPHA_VANTAGE_API_KEY is not set")
        self._bucket.acquire()
        time_from = (date.today() - timedelta(days=lookback_days)).strftime("%Y%m%dT0000")
        params = {
            "function": "NEWS_SENTIMENT", "tickers": self._av_ticker(ticker),
            "time_from": time_from, "sort": "LATEST", "limit": "50", "apikey": self.api_key,
        }
        response = self._client().get(self.BASE, params=params)
        response.raise_for_status()
        data = response.json()
        if "feed" not in data:
            # Alpha Vantage returns {"Information": ...} on a rate-limit or bad symbol.
            raise ProviderDataError(f"Alpha Vantage returned no feed: {data.get('Information') or data}")
        return [self._map(item) for item in (data.get("feed") or [])]

    @staticmethod
    def _map(item: dict[str, Any]) -> dict[str, Any]:
        # NOTE: overall_sentiment_score is intentionally NOT carried (PRD 8.6).
        return {
            "title": item.get("title", ""),
            "summary": item.get("summary", ""),
            "source": item.get("source", ""),
            "url": item.get("url"),
            "published_date": AlphaVantageNews._av_date(item.get("time_published")),
        }


class NewsApiOrg:
    """NewsAPI /v2/everything (PRD 9.1 fallback). Queried by company name, which
    tends to cover Indian companies better than a ticker symbol."""

    name = "newsapi"
    BASE = "https://newsapi.org/v2/everything"

    def __init__(self, *, api_key: str | None = None, http_client: Any = None,
                 timeout: float = 30.0) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("NEWSAPI_KEY", "")
        self._http = http_client
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def _client(self) -> Any:
        if self._http is None:
            self._http = _lazy_httpx(self.timeout)
        return self._http

    def fetch(self, ticker: str, *, name: str | None = None,
              lookback_days: int = 60) -> list[dict[str, Any]]:
        if not self.available():
            raise ProviderUnavailable("NEWSAPI_KEY is not set")
        query = name or ticker
        params = {
            "q": query, "from": (date.today() - timedelta(days=lookback_days)).isoformat(),
            "sortBy": "publishedAt", "language": "en", "pageSize": "50", "apiKey": self.api_key,
        }
        response = self._client().get(self.BASE, params=params)
        response.raise_for_status()
        data = response.json()
        if data.get("status") != "ok":
            raise ProviderDataError(f"NewsAPI error: {data.get('message') or data}")
        return [self._map(item) for item in (data.get("articles") or [])]

    @staticmethod
    def _map(item: dict[str, Any]) -> dict[str, Any]:
        published = item.get("publishedAt")
        return {
            "title": item.get("title", ""),
            "summary": item.get("description", ""),
            "source": (item.get("source") or {}).get("name", ""),
            "url": item.get("url"),
            "published_date": published[:10] if published else None,
        }
