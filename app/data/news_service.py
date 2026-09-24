"""News retrieval service (PRD 9.1, 9.2).

Sits between the news providers and the News Analysis Agent. Owns the fallback
chain (Alpha Vantage → NewsAPI) and the disk cache (news TTL of 6 hours, PRD
9.2), and exposes the ``news_source`` adapter the orchestrator expects — a
callable that turns a :class:`CompanyIdentity` into raw articles.

No news is not an error: on a rate limit, a missing key, or thin Indian
coverage, the service returns an empty list, and the news pillar scores a
neutral 50 with an "insufficient coverage" flag rather than failing the run.
"""

from __future__ import annotations

from typing import Any

from app.data.cache import DiskCache
from app.data.providers.base import ProviderError
from app.data.providers.news import AlphaVantageNews, NewsApiOrg, NewsProvider

NEWS_KIND = "news"


class NewsService:
    def __init__(
        self,
        providers: list[NewsProvider] | None = None,
        *,
        cache: DiskCache | None = None,
        use_cache: bool = True,
        lookback_days: int = 60,
    ) -> None:
        self.providers: list[NewsProvider] = (
            providers if providers is not None else [AlphaVantageNews(), NewsApiOrg()]
        )
        self.cache = cache if cache is not None else DiskCache(enabled=use_cache)
        self.lookback_days = lookback_days

    def fetch(self, ticker: str, *, name: str | None = None,
              lookback_days: int | None = None) -> list[dict[str, Any]]:
        """Raw articles for a company, from the first provider that yields any."""
        lookback = lookback_days or self.lookback_days
        key = ticker or name or "unknown"
        for provider in self.providers:
            cached = self.cache.get(provider.name, NEWS_KIND, key)
            if cached is not None:
                return cached.get("articles", [])
            if not provider.available():
                continue
            try:
                articles = provider.fetch(ticker, name=name, lookback_days=lookback)
            except ProviderError:
                continue
            except Exception:  # noqa: BLE001 — a provider bug must not sink the analysis
                continue
            if articles:
                self.cache.set(provider.name, NEWS_KIND, key, {"articles": articles})
                return articles
        return []   # no coverage -> the news pillar goes neutral, not failed

    def news_source(self, identity: Any) -> list[dict[str, Any]]:
        """Adapter matching the orchestrator's ``NewsSource`` signature."""
        return self.fetch(identity.ticker, name=getattr(identity, "legal_name", None),
                          lookback_days=self.lookback_days)
