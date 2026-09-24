"""yfinance provider — the fallback statement/market source (PRD 9.1).

In the source matrix yfinance is the fallback behind Financial Modeling Prep;
until the FMP client lands it is the *only* live source, and the fallback chain
in :mod:`app.data.service` is built so that inserting FMP ahead of it later is
a one-line change.

The dependency is imported lazily. The Phase 1 computation layer runs on three
packages (pydantic, PyYAML, pytest) with no network and no yfinance, and that
must stay true: importing this module costs nothing, and only an actual fetch
pulls yfinance in. If it is not installed, the provider reports itself
unavailable and the service moves on — it never crashes an analysis.

The provider does exactly one thing: turn yfinance's pandas frames into the
plain, JSON-serialisable *raw payload* of :mod:`app.data.providers.base`,
preserving yfinance's own labels, units and signs. All interpretation — field
mapping, crore conversion, sign correction — is deferred to
:mod:`app.data.normalise`. That keeps this module free of canonical-model
knowledge and keeps the heavy pandas handling out of everything that is unit
tested.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timezone
from typing import Any

from app.data.providers.base import ProviderDataError, ProviderUnavailable
from app.data.rate_limiter import TokenBucket, retry

# yfinance has no published hard rate limit, but it fronts Yahoo endpoints that
# will throttle a burst. A gentle steady rate keeps us clear of that.
_DEFAULT_BUCKET = dict(capacity=5.0, refill_rate=1.0)

_QUOTE_FIELDS: dict[str, tuple[str, ...]] = {
    "current_price": ("currentPrice", "regularMarketPrice", "previousClose"),
    "market_cap": ("marketCap",),
    "shares_outstanding": ("sharesOutstanding",),
    "beta": ("beta",),
    "week_52_high": ("fiftyTwoWeekHigh",),
    "week_52_low": ("fiftyTwoWeekLow",),
    "avg_daily_volume": ("averageVolume", "averageDailyVolume10Day"),
}


def _safe_attr(obj: Any, name: str, default: Any = None) -> Any:
    """``getattr`` that treats a *raising* lazy property as absent.

    yfinance's ``.info`` and statement frames are properties that hit the
    network on access and can raise (a timeout, a JSON error, a Yahoo change).
    Plain ``getattr`` only swallows ``AttributeError``, so a blip there would
    propagate as an unexpected exception; this degrades it to a clean miss, and
    the provider then reports no data so the service can fall through."""
    try:
        return getattr(obj, name, default)
    except Exception:  # noqa: BLE001 — any failure to read a lazy property is a miss
        return default


def _default_ticker_factory(symbol: str) -> Any:
    """Construct a real ``yfinance.Ticker``, importing yfinance only now."""
    try:
        import yfinance  # noqa: PLC0415 — deliberately lazy; see module docstring
    except ImportError as exc:  # pragma: no cover — exercised only without the dep
        raise ProviderUnavailable(
            "yfinance is not installed; install it (pip install yfinance) to use "
            "the yfinance provider"
        ) from exc
    return yfinance.Ticker(symbol)


def _frame_to_periods(frame: Any) -> dict[str, dict[str, float]]:
    """A yfinance statement frame → ``{period_end_iso: {label: value}}``.

    Works by duck typing on the pandas frame (``.columns``, ``frame[col]``,
    ``.items()``) so the module never has to ``import pandas`` itself. A column
    is a period-end timestamp; each cell is a raw absolute amount. NaNs and
    non-numeric cells are dropped rather than carried as noise.
    """
    out: dict[str, dict[str, float]] = {}
    if frame is None or getattr(frame, "empty", True):
        return out
    for col in frame.columns:
        iso = col.date().isoformat() if hasattr(col, "date") else str(col)[:10]
        column = frame[col]
        cells: dict[str, float] = {}
        for label, value in column.items():
            if value is None:
                continue
            try:
                number = float(value)
            except (TypeError, ValueError):
                continue
            if number != number:  # NaN
                continue
            cells[str(label)] = number
        out[iso] = cells
    return out


def _merge_periods(
    income: dict[str, dict[str, float]],
    balance: dict[str, dict[str, float]],
    cashflow: dict[str, dict[str, float]],
) -> list[dict[str, Any]]:
    """Assemble per-statement period maps into the raw payload's period list."""
    period_ends = sorted(
        set(income) | set(balance) | set(cashflow), reverse=True
    )
    periods: list[dict[str, Any]] = []
    for end in period_ends:
        periods.append(
            {
                "period_end": end,
                "income": income.get(end, {}),
                "balance": balance.get(end, {}),
                "cashflow": cashflow.get(end, {}),
            }
        )
    return periods


class YFinanceProvider:
    """Fetches raw statements and market data from yfinance.

    ``ticker_factory`` is injectable so tests drive the whole extraction with a
    fake ``Ticker`` and never touch the network or require yfinance to be
    installed. In production it defaults to constructing a real
    ``yfinance.Ticker`` lazily.
    """

    name = "yfinance"

    def __init__(
        self,
        ticker_factory: Callable[[str], Any] | None = None,
        *,
        bucket: TokenBucket | None = None,
        currency: str = "INR",
        retry_attempts: int = 3,
    ) -> None:
        self._factory = ticker_factory or _default_ticker_factory
        self._bucket = bucket or TokenBucket(**_DEFAULT_BUCKET)
        self._currency = currency
        self._retry_attempts = retry_attempts

    def available(self) -> bool:
        """True when yfinance can be imported (or a fake factory was injected)."""
        if self._factory is not _default_ticker_factory:
            return True
        try:
            import yfinance  # noqa: F401, PLC0415
        except ImportError:
            return False
        return True

    def _ticker(self, symbol: str) -> Any:
        self._bucket.acquire()
        return retry(
            lambda: self._factory(symbol),
            attempts=self._retry_attempts,
            exceptions=(OSError, ProviderDataError, ValueError, RuntimeError),
        )

    def _now(self) -> str:
        return datetime.now(timezone.utc).isoformat()

    def _profile(self, info: dict[str, Any]) -> dict[str, Any]:
        return {
            "long_name": info.get("longName") or info.get("shortName"),
            "sector": info.get("sector"),
            "industry": info.get("industry"),
            "symbol": info.get("symbol"),
        }

    def fetch_statements(self, ticker: str) -> dict[str, Any]:
        handle = self._ticker(ticker)
        income = _frame_to_periods(_safe_attr(handle, "income_stmt"))
        # The income statement is load-bearing (revenue, PAT). If it came back
        # empty — a wrong symbol, or Yahoo rate-limiting mid-sequence — treat it
        # as a clean data failure rather than emitting periods that cannot be
        # built into an IncomeStatement.
        if not income:
            raise ProviderDataError(
                f"yfinance returned no income statement for '{ticker}' — the symbol "
                f"may be wrong/unavailable, or Yahoo rate-limited the request."
            )
        annual = _merge_periods(
            income,
            _frame_to_periods(_safe_attr(handle, "balance_sheet")),
            _frame_to_periods(_safe_attr(handle, "cashflow")),
        )
        quarterly = _merge_periods(
            _frame_to_periods(_safe_attr(handle, "quarterly_income_stmt")),
            _frame_to_periods(_safe_attr(handle, "quarterly_balance_sheet")),
            _frame_to_periods(_safe_attr(handle, "quarterly_cashflow")),
        )
        info = dict(_safe_attr(handle, "info", {}) or {})
        currency = info.get("financialCurrency") or self._currency
        return {
            "source": self.name,
            "symbol": ticker,
            "currency": currency,
            "as_of": self._now(),
            "profile": self._profile(info),
            "annual": annual,
            "quarterly": quarterly,
        }

    def fetch_market(self, ticker: str) -> dict[str, Any]:
        handle = self._ticker(ticker)
        info = dict(_safe_attr(handle, "info", {}) or {})
        quote: dict[str, Any] = {}
        for field, candidates in _QUOTE_FIELDS.items():
            for key in candidates:
                if info.get(key) is not None:
                    quote[field] = info[key]
                    break
        if quote.get("current_price") is None:
            raise ProviderDataError(
                f"yfinance returned no price for '{ticker}'."
            )
        history = self._price_history(handle)
        return {
            "source": self.name,
            "symbol": ticker,
            "currency": info.get("financialCurrency") or self._currency,
            "as_of": self._now(),
            "quote": quote,
            "price_history": history,
        }

    def _price_history(self, handle: Any) -> list[list[Any]]:
        """5y daily closes as ``[[iso_date, close], ...]``, best-effort."""
        history_fn = _safe_attr(handle, "history")
        if not callable(history_fn):
            return []
        try:
            frame = history_fn(period="5y", interval="1d")
        except Exception:  # noqa: BLE001 — price history is best-effort; a failure just omits it
            return []
        if frame is None or getattr(frame, "empty", True) or "Close" not in getattr(frame, "columns", []):
            return []
        points: list[list[Any]] = []
        closes = frame["Close"]
        for index, value in closes.items():
            iso = index.date().isoformat() if hasattr(index, "date") else str(index)[:10]
            try:
                points.append([iso, float(value)])
            except (TypeError, ValueError):
                continue
        return points
