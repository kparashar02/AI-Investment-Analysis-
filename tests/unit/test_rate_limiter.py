"""Token bucket and retry (app/data/rate_limiter.py).

Time and randomness are injected so the throttle and backoff are tested
deterministically, with no real sleeping and no dependence on wall-clock or a
seeded global RNG.
"""

from __future__ import annotations

import random

import pytest

from app.data.rate_limiter import TokenBucket, first_success, retry


class FakeClock:
    def __init__(self, t: float = 0.0) -> None:
        self.t = t

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class RecordingSleeper:
    """Records requested sleeps and, when tied to a clock, advances it — so a
    bucket 'waits' in virtual time exactly as long as it asked to."""

    def __init__(self, clock: FakeClock | None = None) -> None:
        self.calls: list[float] = []
        self.clock = clock

    def __call__(self, seconds: float) -> None:
        self.calls.append(seconds)
        if self.clock is not None:
            self.clock.advance(seconds)


# --- token bucket ----------------------------------------------------------

def test_full_bucket_grants_immediate_burst():
    clock = FakeClock()
    sleeper = RecordingSleeper(clock)
    bucket = TokenBucket(3, 1, clock=clock, sleeper=sleeper, start_full=True)
    for _ in range(3):
        assert bucket.acquire() == 0.0  # three tokens in hand, no waiting
    assert sleeper.calls == []


def test_bucket_throttles_once_drained():
    clock = FakeClock()
    sleeper = RecordingSleeper(clock)
    bucket = TokenBucket(2, refill_rate=2.0, clock=clock, sleeper=sleeper)
    bucket.acquire()
    bucket.acquire()  # drained
    waited = bucket.acquire()  # must wait for one token at 2 tokens/sec -> 0.5s
    assert waited == pytest.approx(0.5)
    assert sleeper.calls == [pytest.approx(0.5)]


def test_bucket_refills_over_time():
    clock = FakeClock()
    sleeper = RecordingSleeper(clock)
    bucket = TokenBucket(5, refill_rate=1.0, clock=clock, sleeper=sleeper, start_full=False)
    assert bucket.tokens == pytest.approx(0.0)
    clock.advance(3)
    assert bucket.tokens == pytest.approx(3.0)
    clock.advance(100)
    assert bucket.tokens == pytest.approx(5.0)  # capped at capacity


def test_acquire_more_than_capacity_is_an_error():
    bucket = TokenBucket(2, 1)
    with pytest.raises(ValueError):
        bucket.acquire(3)


def test_bucket_rejects_nonpositive_config():
    with pytest.raises(ValueError):
        TokenBucket(0, 1)
    with pytest.raises(ValueError):
        TokenBucket(1, 0)


# --- retry -----------------------------------------------------------------

def test_retry_returns_first_success_without_sleeping():
    sleeper = RecordingSleeper()
    result = retry(lambda: 42, sleeper=sleeper)
    assert result == 42
    assert sleeper.calls == []


def test_retry_recovers_after_transient_failures():
    sleeper = RecordingSleeper()
    attempts = {"n": 0}

    def flaky():
        attempts["n"] += 1
        if attempts["n"] < 3:
            raise OSError("transient")
        return "ok"

    result = retry(flaky, attempts=3, exceptions=OSError, sleeper=sleeper,
                   rng=random.Random(0))
    assert result == "ok"
    assert attempts["n"] == 3
    assert len(sleeper.calls) == 2  # slept before attempts 2 and 3


def test_retry_reraises_after_exhausting_attempts():
    sleeper = RecordingSleeper()

    def always_fails():
        raise OSError("down")

    with pytest.raises(OSError, match="down"):
        retry(always_fails, attempts=3, exceptions=OSError, sleeper=sleeper,
              rng=random.Random(0))
    assert len(sleeper.calls) == 2  # slept between the 3 attempts, not after the last


def test_retry_backoff_is_exponential_and_capped():
    sleeper = RecordingSleeper()

    def always_fails():
        raise ValueError("x")

    # jitter=0 so the delays are exactly base * 2**n, capped at max_delay.
    with pytest.raises(ValueError):
        retry(always_fails, attempts=5, base_delay=1.0, max_delay=4.0, jitter=0.0,
              exceptions=ValueError, sleeper=sleeper, rng=random.Random(0))
    assert sleeper.calls == [1.0, 2.0, 4.0, 4.0]  # 1,2,4, then capped at 4


def test_retry_rejects_zero_attempts():
    with pytest.raises(ValueError):
        retry(lambda: 1, attempts=0)


# --- first_success (fallback chain primitive) ------------------------------

def test_first_success_returns_first_non_raising():
    def boom():
        raise ValueError("no")

    assert first_success([boom, lambda: "b", lambda: "c"], exceptions=ValueError) == "b"


def test_first_success_reraises_when_all_fail():
    def boom():
        raise ValueError("no")

    with pytest.raises(ValueError):
        first_success([boom, boom], exceptions=ValueError)


def test_first_success_with_no_candidates_is_an_error():
    with pytest.raises(ValueError):
        first_success([])
