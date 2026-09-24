"""Rate limiting and retry — a first-class design constraint (PRD 9.2).

Free-tier data APIs are metered aggressively (Alpha Vantage ~25 requests/day,
FMP ~250/day). The engine's reproducibility guarantee is only affordable if
the system almost never has to hit the network, and never hammers a provider
into a hard block. Two mechanisms live here:

* :class:`TokenBucket` — a per-provider throttle that smooths request bursts to
  a sustainable steady rate.
* :func:`retry` — exponential backoff with jitter around a transient failure,
  so a momentary blip escalates to the fallback source only after genuine
  retries, not on the first hiccup.

Both take their clock, their sleep and their randomness by injection. That is
not incidental: it keeps the whole module unit-testable without real time
passing, and it keeps ``import random`` out of anything the determinism tests
could ever reach.
"""

from __future__ import annotations

import random
import time
from collections.abc import Callable, Iterable
from typing import TypeVar

T = TypeVar("T")

Clock = Callable[[], float]
Sleeper = Callable[[float], None]


class TokenBucket:
    """A classic token bucket.

    ``capacity`` tokens accumulate at ``refill_rate`` tokens per second, capped
    at ``capacity``. :meth:`acquire` consumes one token, sleeping until one is
    available. Constructing the bucket full lets a cold start issue an
    immediate burst up to ``capacity`` before throttling to the steady rate.
    """

    def __init__(
        self,
        capacity: float,
        refill_rate: float,
        *,
        clock: Clock = time.monotonic,
        sleeper: Sleeper = time.sleep,
        start_full: bool = True,
    ) -> None:
        if capacity <= 0:
            raise ValueError("capacity must be positive")
        if refill_rate <= 0:
            raise ValueError("refill_rate must be positive")
        self.capacity = float(capacity)
        self.refill_rate = float(refill_rate)
        self._clock = clock
        self._sleeper = sleeper
        self._tokens = float(capacity) if start_full else 0.0
        self._last = clock()

    def _replenish(self) -> None:
        now = self._clock()
        elapsed = now - self._last
        if elapsed > 0:
            self._tokens = min(self.capacity, self._tokens + elapsed * self.refill_rate)
            self._last = now

    @property
    def tokens(self) -> float:
        """Tokens currently available (after replenishing for elapsed time)."""
        self._replenish()
        return self._tokens

    def acquire(self, amount: float = 1.0) -> float:
        """Consume ``amount`` tokens, sleeping until they are available.

        Returns the time spent waiting, in seconds — zero when a token was
        already in hand. ``amount`` may not exceed ``capacity``; a single
        request can never be larger than the bucket that meters it.
        """
        if amount > self.capacity:
            raise ValueError(
                f"cannot acquire {amount} tokens from a bucket of capacity "
                f"{self.capacity}"
            )
        self._replenish()
        waited = 0.0
        if self._tokens < amount:
            deficit = amount - self._tokens
            wait = deficit / self.refill_rate
            self._sleeper(wait)
            waited = wait
            self._replenish()
        self._tokens -= amount
        return waited


def retry(
    fn: Callable[[], T],
    *,
    attempts: int = 3,
    base_delay: float = 0.5,
    max_delay: float = 8.0,
    jitter: float = 0.1,
    exceptions: type[BaseException] | tuple[type[BaseException], ...] = Exception,
    sleeper: Sleeper = time.sleep,
    rng: random.Random | None = None,
) -> T:
    """Call ``fn`` with exponential backoff, re-raising the last error if every
    attempt fails.

    The delay before attempt *n* (1-indexed) is
    ``min(max_delay, base_delay * 2**(n-1))`` plus a uniform jitter of up to
    ``jitter`` seconds, which de-correlates retries across concurrent callers
    so they do not all wake and re-hit the provider on the same beat.

    ``rng`` is injectable so a test can pin the jitter; the module-level
    randomness never leaks into the engine, which forbids ``import random``.
    """
    if attempts < 1:
        raise ValueError("attempts must be at least 1")
    _rng = rng or random.Random()
    last: BaseException | None = None
    for attempt in range(1, attempts + 1):
        try:
            return fn()
        except exceptions as exc:  # noqa: BLE001 — deliberately caller-configurable
            last = exc
            if attempt == attempts:
                break
            delay = min(max_delay, base_delay * (2 ** (attempt - 1)))
            delay += _rng.uniform(0.0, jitter)
            sleeper(delay)
    assert last is not None  # unreachable: attempts >= 1 guarantees a caught error
    raise last


def first_success(
    calls: Iterable[Callable[[], T]],
    *,
    exceptions: type[BaseException] | tuple[type[BaseException], ...] = Exception,
) -> T:
    """Return the result of the first call that does not raise.

    Used to walk a provider fallback chain: each element is a zero-argument
    thunk that fetches from one source. Re-raises the final exception if every
    source fails, so the caller still sees a real error rather than ``None``.
    """
    last: BaseException | None = None
    any_call = False
    for call in calls:
        any_call = True
        try:
            return call()
        except exceptions as exc:  # noqa: BLE001
            last = exc
    if not any_call:
        raise ValueError("first_success called with no candidates")
    assert last is not None
    raise last
