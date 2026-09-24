"""The provider contract.

A provider is anything that can turn a ticker into a *raw payload* — a plain,
JSON-serialisable dict carrying one external source's numbers under that
source's own field names, in that source's own units and sign conventions.
Nothing here maps onto the canonical statement schema; that is
:mod:`app.data.normalise`'s job. Keeping the two apart is what lets the disk
cache store exactly what the API returned (so a cached response is
re-normalisable if the mapping is later corrected) and lets a second source
be added without touching a line of engine or model code.

Raw statements payload
----------------------
::

    {
      "source":   "yfinance",
      "symbol":   "TCS.NS",
      "currency": "INR",                 # reporting currency of the numbers
      "as_of":    "2026-09-16T12:00:00+00:00",
      "profile":  {"long_name": "...", "sector": "...", "industry": "..."},
      "annual": [
         {"period_end": "2026-03-31",
          "income":   {"<source label>": <float, absolute currency units>, ...},
          "balance":  {"<source label>": <float>, ...},
          "cashflow": {"<source label>": <float>, ...}},
         ...
      ],
      "quarterly": [ ... same shape ... ]
    }

Raw market payload
------------------
::

    {
      "source":   "yfinance",
      "symbol":   "TCS.NS",
      "currency": "INR",
      "as_of":    "2026-09-16T12:00:00+00:00",
      "quote": {"current_price": <float, per share>,
                "market_cap":    <float, absolute currency units>,
                "shares_outstanding": <float, absolute share count>,
                "beta": <float>, "week_52_high": <float>, "week_52_low": <float>,
                "avg_daily_volume": <float>},
      "price_history": [["2021-09-16", <float close>], ...]
    }

Every value is a raw magnitude in the source's own units. Absolute→crore
conversion and sign correction happen in normalisation, never here.
"""

from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


class ProviderError(Exception):
    """Base class for every provider failure the service knows how to handle.

    A ``ProviderError`` is a signal to the :class:`~app.data.service.DataService`
    to move on to the next source in the fallback chain, never to crash.
    """


class ProviderUnavailable(ProviderError):
    """The provider cannot be used at all — its dependency is not installed,
    or its API key is missing. Distinct from a failed fetch so the caller can
    tell "not configured" from "configured but the request failed"."""


class ProviderDataError(ProviderError):
    """The provider was reachable but returned nothing usable for this ticker
    (empty statements, an unknown symbol, a transport error after retries)."""


@runtime_checkable
class StatementProvider(Protocol):
    """The interface :class:`~app.data.service.DataService` fetches through.

    Implementations must be side-effect-free to construct: importing a heavy
    dependency or touching the network belongs in :meth:`available` and the
    fetch methods, not ``__init__``, so that a provider that is merely present
    in the chain costs nothing until it is actually used.
    """

    name: str

    def available(self) -> bool:
        """Whether this provider can run at all right now (dependency present,
        credentials configured). Checked before any fetch is attempted."""
        ...

    def fetch_statements(self, ticker: str) -> dict[str, Any]:
        """Return the raw statements payload for ``ticker``.

        Raises :class:`ProviderUnavailable` if the provider is not usable, or
        :class:`ProviderDataError` if the fetch produced nothing usable.
        """
        ...

    def fetch_market(self, ticker: str) -> dict[str, Any]:
        """Return the raw market payload for ``ticker``. Same failure contract
        as :meth:`fetch_statements`."""
        ...
