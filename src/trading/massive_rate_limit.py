from __future__ import annotations

import asyncio
import os
import time
from collections import deque


def _max_calls_per_minute() -> int:
    raw = os.environ.get("MASSIVE_RATE_LIMIT_PER_MINUTE", "5").strip()
    try:
        value = int(raw)
    except ValueError:
        return 5
    return max(1, value)


class MinuteRateLimiter:
    """Rolling-window limiter for Massive REST calls (default 5/minute)."""

    def __init__(self, max_calls: int | None = None, window_seconds: float = 60.0) -> None:
        self.max_calls = max_calls if max_calls is not None else _max_calls_per_minute()
        self.window_seconds = window_seconds
        self._timestamps: deque[float] = deque()
        self._lock = asyncio.Lock()

    def _prune(self, now: float) -> None:
        cutoff = now - self.window_seconds
        while self._timestamps and self._timestamps[0] <= cutoff:
            self._timestamps.popleft()

    def remaining(self) -> int:
        now = time.monotonic()
        self._prune(now)
        return max(0, self.max_calls - len(self._timestamps))

    async def try_acquire(self) -> bool:
        """Reserve a call slot without waiting. Returns False when quota is exhausted."""
        async with self._lock:
            now = time.monotonic()
            self._prune(now)
            if len(self._timestamps) >= self.max_calls:
                return False
            self._timestamps.append(now)
            return True

    async def wait_for_slot(self, timeout: float | None = None) -> bool:
        """Wait until a slot opens or timeout elapses."""
        deadline = None if timeout is None else time.monotonic() + timeout
        while True:
            if await self.try_acquire():
                return True
            now = time.monotonic()
            if deadline is not None and now >= deadline:
                return False
            async with self._lock:
                self._prune(now)
                if not self._timestamps:
                    sleep_for = 0.05
                else:
                    sleep_for = max(0.05, self._timestamps[0] + self.window_seconds - now)
            if deadline is not None:
                sleep_for = min(sleep_for, max(0.0, deadline - time.monotonic()))
            await asyncio.sleep(sleep_for)

    async def release_last(self) -> None:
        """Return the most recent slot when a call was rejected by the remote API."""
        async with self._lock:
            self._prune(time.monotonic())
            if self._timestamps:
                self._timestamps.pop()

    def snapshot(self) -> dict[str, int | float]:
        return {
            "limit_per_minute": self.max_calls,
            "remaining": self.remaining(),
            "window_seconds": int(self.window_seconds),
        }


_limiter = MinuteRateLimiter()


def get_massive_rate_limiter() -> MinuteRateLimiter:
    return _limiter
