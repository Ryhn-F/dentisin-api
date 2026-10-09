"""In-memory token-bucket rate limiter.

Limits (defaults, configurable via settings):
- per client IP, all routes except /healthz and /readyz: API_RATE_LIMIT_PER_MINUTE
  tokens/minute refill, bucket size API_RATE_LIMIT_BURST;
- per authenticated subject on /v1: the same numbers, separate buckets.

Single-process only: with N replicas the effective limit is N times higher.
That is acceptable for an internal admin/reviewer API (handful of users);
use a shared store (e.g. Redis) before exposing anything to the public.
"""

from __future__ import annotations

import math
import threading
import time
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass


@dataclass
class _Bucket:
    tokens: float
    updated: float


class TokenBucketLimiter:
    def __init__(
        self,
        rate_per_minute: int,
        burst: int,
        clock: Callable[[], float] = time.monotonic,
        max_keys: int = 10_000,
    ) -> None:
        self._rate = rate_per_minute / 60.0
        self._burst = float(burst)
        self._clock = clock
        self._max_keys = max_keys
        self._buckets: OrderedDict[str, _Bucket] = OrderedDict()
        self._lock = threading.Lock()

    def acquire(self, key: str) -> tuple[bool, int]:
        """Take one token. Returns (allowed, retry_after_seconds)."""
        now = self._clock()
        with self._lock:
            bucket = self._buckets.get(key)
            if bucket is None:
                bucket = _Bucket(self._burst, now)
                self._buckets[key] = bucket
                if len(self._buckets) > self._max_keys:
                    self._buckets.popitem(last=False)  # bounded memory: evict oldest
            else:
                self._buckets.move_to_end(key)
                bucket.tokens = min(
                    self._burst, bucket.tokens + (now - bucket.updated) * self._rate
                )
                bucket.updated = now
            if bucket.tokens >= 1.0:
                bucket.tokens -= 1.0
                return True, 0
            retry = math.ceil((1.0 - bucket.tokens) / self._rate) if self._rate > 0 else 60
            return False, max(1, retry)
