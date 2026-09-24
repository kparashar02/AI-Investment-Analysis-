"""The data layer's single entry point.

:class:`DataService` sits between the providers and the engine. It owns the
fallback chain, the disk cache and the normalisation step, and it hands the
engine exactly what :func:`app.engine.registry.compute_metric_set` expects: a
canonical :class:`FinancialStatements` (with a :class:`DataQualityReport`
attached) and an optional :class:`MarketData`.

The fallback contract (PRD 8.3, 9.1): try each provider in order; a
:class:`ProviderError` means "this source could not help, move on"; if every
source is exhausted, raise :class:`DataUnavailable` rather than return partial
or synthesised data. The cache is consulted per source before that source is
called, so a warm cache short-circuits the network entirely — which is what
makes repeat analyses free and a demo reproducible (PRD 9.2, NF4).

Today the chain is ``[yfinance]``. When the FMP client lands it is prepended:
``providers = [FMPProvider(), YFinanceProvider()]`` and nothing else changes,
because providers already speak the same raw-payload contract.
"""

from __future__ import annotations

from typing import Any

from app.data.cache import DiskCache
from app.data.normalise import (
    build_data_quality,
    normalise_market,
    normalise_statements,
)
from app.data.providers.base import ProviderError, StatementProvider
from app.data.providers.yfinance_fallback import YFinanceProvider
from app.models.statements import FinancialStatements, MarketData


class DataUnavailable(RuntimeError):
    """Every provider in the chain failed for this ticker. Carries the per-source
    errors so the caller can report exactly what was tried and why it failed."""

    def __init__(self, ticker: str, kind: str, attempts: dict[str, str]) -> None:
        self.ticker = ticker
        self.kind = kind
        self.attempts = attempts
        detail = "; ".join(f"{src}: {msg}" for src, msg in attempts.items()) or "no providers"
        super().__init__(f"could not fetch {kind} for '{ticker}' ({detail})")


class DataService:
    """Fetch, cache and normalise company data through a provider fallback chain."""

    def __init__(
        self,
        providers: list[StatementProvider] | None = None,
        *,
        cache: DiskCache | None = None,
        use_cache: bool = True,
    ) -> None:
        self.providers: list[StatementProvider] = (
            providers if providers is not None else [YFinanceProvider()]
        )
        self.cache = cache if cache is not None else DiskCache(enabled=use_cache)

    # -- raw fetch with cache + fallback ------------------------------------

    def _fetch_raw(
        self, ticker: str, kind: str, method: str
    ) -> dict[str, Any]:
        """Walk the provider chain for one ``kind`` ('statements' | 'market').

        For each provider: serve a fresh cache entry if present, else call the
        provider, cache the result, and return it. A provider that raises a
        :class:`ProviderError` (or is unavailable) is recorded and skipped.
        """
        attempts: dict[str, str] = {}
        for provider in self.providers:
            source = provider.name
            cached = self.cache.get(source, kind, ticker)
            if cached is not None:
                cached["_from_cache"] = True
                cached["_cache_age_seconds"] = self.cache.age_seconds(source, kind, ticker)
                return cached

            if not provider.available():
                attempts[source] = "unavailable (dependency or credentials missing)"
                continue

            try:
                payload = getattr(provider, method)(ticker)
            except ProviderError as exc:
                attempts[source] = str(exc)
                continue
            except Exception as exc:  # noqa: BLE001 — a provider bug must not crash the chain
                attempts[source] = f"unexpected {type(exc).__name__}: {exc}"
                continue

            self.cache.set(source, kind, ticker, payload)
            payload["_from_cache"] = False
            return payload

        raise DataUnavailable(ticker, kind, attempts)

    # -- public API ---------------------------------------------------------

    def get_statements(
        self,
        ticker: str,
        *,
        company_name: str | None = None,
        sector: str | None = None,
        industry: str | None = None,
    ) -> FinancialStatements:
        """Canonical statements for ``ticker``, with a data-quality report attached.

        The optional identity hints override whatever the provider's own
        profile block guessed, so a caller that has already resolved the
        company (Phase 3 symbol resolution) stays authoritative.
        """
        raw = self._fetch_raw(ticker, "statements", "fetch_statements")
        statements = normalise_statements(
            raw,
            ticker=ticker,
            company_name=company_name,
            sector=sector,
            industry=industry,
        )
        warnings: list[str] = []
        if raw.get("_from_cache"):
            age = raw.get("_cache_age_seconds")
            note = "served from cache"
            if isinstance(age, (int, float)):
                note += f" (age {age / 3600:.1f}h)"
            warnings.append(note)
        currency = str(raw.get("currency", "INR")).upper()
        if currency != "INR":
            warnings.append(
                f"Source reported statements in {currency}, not INR; figures were "
                f"converted to crore on the assumption of a single currency and "
                f"should be verified (PRD 9.3 currency inconsistency)."
            )
        statements.data_quality = build_data_quality(
            statements,
            sources_used=[str(raw.get("source", "unknown"))],
            extra_warnings=warnings,
        )
        return statements

    def get_market(self, ticker: str) -> MarketData:
        """Canonical market data for ``ticker``."""
        raw = self._fetch_raw(ticker, "market", "fetch_market")
        return normalise_market(raw, ticker=ticker)

    def get_market_or_none(self, ticker: str) -> MarketData | None:
        """Market data, or ``None`` if no source could supply it.

        Market data is not required to compute the ratio sheet (the engine
        degrades and simply marks valuation metrics unavailable), so a caller
        that wants the statements regardless can tolerate its absence."""
        try:
            return self.get_market(ticker)
        except (DataUnavailable, ValueError):
            return None
