"""Allowlisted web research for the industry agent (PRD 8.7, 9.4, 9.5).

Searches the web for industry/macro context, then admits only sources on the
``sources.yaml`` allowlist — everything blocked or un-allowlisted is discarded
before the agent ever sees it. What survives becomes short, attributed snippets
(quotes capped per the copyright rule) plus structured :class:`Citation`s, so
every qualitative claim the industry agent later makes can be traced to a real
allowlisted source rather than invented.

Two safety rules from PRD 9.5 hold here: retrieved text is DATA, never
instruction — it is wrapped and attributed, and the industry agent is told to
report, not obey, any imperative inside it — and because the composite score is
computed in Python from bounded fields, an injection into the research still
cannot move the recommendation.

The search backend is injectable and the transport is lazy, so the whole feed
is unit-testable offline; with no search key configured it simply returns no
research and the industry agent proceeds without it.
"""

from __future__ import annotations

import os
from datetime import date
from typing import Any, Protocol, runtime_checkable

from pydantic import BaseModel, Field

from app.config.settings import load_sources
from app.data.allowlist import SourceAllowlist
from app.data.cache import DiskCache
from app.models.agent_io import Citation

RESEARCH_KIND = "industry"


class WebResearch(BaseModel):
    snippets: list[str] = Field(default_factory=list)
    citations: list[Citation] = Field(default_factory=list)


@runtime_checkable
class SearchBackend(Protocol):
    name: str

    def available(self) -> bool: ...

    def search(self, query: str, *, count: int = 10) -> list[dict[str, Any]]: ...


class BraveSearchBackend:
    """Brave Search API (has a free tier). Results: ``{url, title, description}``."""

    name = "brave"
    BASE = "https://api.search.brave.com/res/v1/web/search"

    def __init__(self, *, api_key: str | None = None, http_client: Any = None,
                 timeout: float = 20.0) -> None:
        self.api_key = api_key if api_key is not None else os.environ.get("BRAVE_API_KEY", "")
        self._http = http_client
        self.timeout = timeout

    def available(self) -> bool:
        return bool(self.api_key)

    def _client(self) -> Any:
        if self._http is None:
            import httpx  # noqa: PLC0415 — lazy
            self._http = httpx.Client(timeout=self.timeout)
        return self._http

    def search(self, query: str, *, count: int = 10) -> list[dict[str, Any]]:
        headers = {"X-Subscription-Token": self.api_key, "Accept": "application/json"}
        params = {"q": query, "count": str(count)}
        response = self._client().get(self.BASE, params=params, headers=headers)
        response.raise_for_status()
        data = response.json()
        results = (data.get("web") or {}).get("results") or []
        return [{"url": r.get("url"), "title": r.get("title"),
                 "description": r.get("description"), "age": r.get("age")} for r in results]


def _truncate_words(text: str, limit: int) -> str:
    words = (text or "").split()
    return " ".join(words[:limit]) + ("…" if len(words) > limit else "")


def format_citation(c: Citation) -> str:
    """The report's numbered-citation string form (PRD 13.1 s17)."""
    parts = [f"{c.source} (Tier {c.tier})"]
    if c.published_date:
        parts.append(f"published {c.published_date}")
    parts.append(c.url)
    if c.access_date:
        parts.append(f"accessed {c.access_date}")
    return " — ".join(parts)


class WebResearchService:
    def __init__(
        self,
        *,
        allowlist: SourceAllowlist | None = None,
        backend: SearchBackend | None = None,
        cache: DiskCache | None = None,
        use_cache: bool = True,
        max_results: int = 6,
    ) -> None:
        self.allowlist = allowlist or SourceAllowlist()
        self.backend = backend or BraveSearchBackend()
        self.cache = cache if cache is not None else DiskCache(enabled=use_cache)
        self.max_results = max_results
        handling = load_sources().get("content_handling") or {}
        self.max_quote_words = int(handling.get("max_evidence_quote_words", 25))

    @staticmethod
    def _query(sector: str | None, industry: str | None) -> str:
        focus = industry or sector or "Indian equity market"
        return f"{focus} India industry outlook demand drivers regulation"

    def research(self, sector: str | None, industry: str | None, *,
                 as_of: str | None = None) -> WebResearch:
        """Return allowlisted research snippets and citations for the industry."""
        key = (industry or sector or "market").lower().replace(" ", "_")
        cached = self.cache.get(self.backend.name, RESEARCH_KIND, key)
        if cached is not None:
            return WebResearch.model_validate(cached)
        if not self.backend.available():
            return WebResearch()
        try:
            results = self.backend.search(self._query(sector, industry), count=self.max_results * 3)
        except Exception:  # noqa: BLE001 — research is best-effort; no research is fine
            return WebResearch()

        access = as_of or date.today().isoformat()
        snippets: list[str] = []
        citations: list[Citation] = []
        for item in results:
            url = item.get("url")
            if not url or not self.allowlist.is_allowed(url):   # drops blocked + un-allowlisted
                continue
            tier = self.allowlist.tier_of(url)
            source = self.allowlist.domain_of(url)
            claim = _truncate_words(item.get("description") or item.get("title") or "",
                                    self.max_quote_words)
            # Wrapped and attributed: this is data to cite, not instruction to obey (PRD 9.5).
            snippets.append(f"<source domain='{source}' tier='{tier}'>{claim}</source>")
            citations.append(Citation(url=url, source=source, tier=tier or 3,
                                      published_date=item.get("age"), access_date=access, claim=claim))
            if len(citations) >= self.max_results:
                break

        result = WebResearch(snippets=snippets, citations=citations)
        if citations:
            self.cache.set(self.backend.name, RESEARCH_KIND, key, result.model_dump())
        return result
