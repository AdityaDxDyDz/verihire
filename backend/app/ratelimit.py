"""In-memory token-bucket rate limiter (per user, per bucket). Good for one container.

For several replicas this would move to Postgres/Redis; the interface stays the same.
"""

from __future__ import annotations

import threading
import time

from .errors import AppError


class RateLimiter:
    def __init__(self, per_minute: int, clock=time.monotonic):
        self.capacity = max(1, per_minute)
        self.rate = self.capacity / 60.0
        self.clock = clock
        self._buckets: dict[str, tuple[float, float]] = {}
        self._lock = threading.Lock()

    def check(self, key: str) -> None:
        """Consume one token or raise 429 with Retry-After."""
        now = self.clock()
        with self._lock:
            tokens, last = self._buckets.get(key, (float(self.capacity), now))
            tokens = min(self.capacity, tokens + (now - last) * self.rate)
            if tokens < 1:
                wait = int((1 - tokens) / self.rate) + 1
                self._buckets[key] = (tokens, now)
                raise AppError(429, "rate_limited", f"Too many requests. Retry in {wait} s.",
                               headers={"Retry-After": str(wait)})
            self._buckets[key] = (tokens - 1, now)
            if len(self._buckets) > 10_000:  # bound memory
                self._buckets.pop(next(iter(self._buckets)))
