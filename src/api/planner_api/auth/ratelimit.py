"""A small in-process sliding-window limiter for the sign-in endpoint."""

from __future__ import annotations

import time
from collections import defaultdict, deque

from planner_api.errors import ErrorCode, ProblemError


class RateLimiter:
    def __init__(self, limit: int, window_seconds: float) -> None:
        self.limit = limit
        self.window = window_seconds
        self._hits: dict[str, deque[float]] = defaultdict(deque)

    def check(self, key: str) -> None:
        now = time.monotonic()
        hits = self._hits[key]
        while hits and now - hits[0] > self.window:
            hits.popleft()
        if len(hits) >= self.limit:
            retry = int(self.window - (now - hits[0])) + 1
            raise ProblemError(
                429,
                ErrorCode.ACCOUNT_LOCKED,
                "Too many sign-in attempts from this address. Try again later.",
                headers={"Retry-After": str(retry)},
            )
        hits.append(now)
        if len(self._hits) > 10_000:  # keep the table small under a scan
            self._hits = defaultdict(deque, {k: v for k, v in self._hits.items() if v})
