"""Company typeahead search via yfinance's ``Search`` (Yahoo autocomplete).

Backs the search box in the web UI: as the user types, the browser asks
``/api/search`` and gets a short list of listed companies to pick from. Only
equities are returned, Indian listings (NSE/BSE) first, since that is the
market the pipeline is built for.

yfinance is imported lazily, like the data provider, so importing this module
costs nothing and the API tests can inject a fake. Any failure — no network,
Yahoo throttling, a schema change — degrades to an empty list; a typeahead must
never break the page.
"""

from __future__ import annotations

import time
from typing import Any

_INDIAN_EXCHANGES = {"NSI", "BSE"}
_CACHE_TTL_S = 600.0
_CACHE_MAX = 512
_cache: dict[tuple[str, int], tuple[float, list[dict[str, str]]]] = {}


def _to_hit(quote: dict[str, Any]) -> dict[str, str] | None:
    symbol = quote.get("symbol")
    if not symbol or quote.get("quoteType") != "EQUITY":
        return None
    return {
        "symbol": symbol,
        "name": quote.get("longname") or quote.get("shortname") or symbol,
        "exchange": quote.get("exchDisp") or quote.get("exchange") or "",
        "_indian": "1" if quote.get("exchange") in _INDIAN_EXCHANGES else "",
    }


def search_companies(query: str, limit: int = 8) -> list[dict[str, str]]:
    """Return up to ``limit`` ``{symbol, name, exchange}`` equity matches."""
    query = query.strip()
    if len(query) < 2:
        return []
    key = (query.lower(), limit)
    cached = _cache.get(key)
    if cached and time.monotonic() - cached[0] < _CACHE_TTL_S:
        return cached[1]

    try:
        import yfinance as yf  # noqa: PLC0415 — lazy, see module docstring

        quotes = yf.Search(query, max_results=max(limit * 2, 10), news_count=0,
                           lists_count=0, enable_fuzzy_query=True).quotes or []
    except Exception:  # noqa: BLE001 — typeahead is best-effort
        return []

    hits = [h for h in (_to_hit(q) for q in quotes) if h]
    # Stable sort keeps Yahoo's relevance order within each group.
    hits.sort(key=lambda h: not h["_indian"])
    result = [{k: v for k, v in h.items() if k != "_indian"} for h in hits[:limit]]

    if len(_cache) >= _CACHE_MAX:
        _cache.clear()
    _cache[key] = (time.monotonic(), result)
    return result
