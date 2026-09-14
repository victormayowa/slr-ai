"""Fixed-window rate limits per client IP: shared through Redis when REDIS_URL is set, in-process otherwise."""

import logging
import os
import threading
import time
from collections.abc import Callable

import redis
from fastapi import HTTPException, Request

logger = logging.getLogger(__name__)


class _MemoryCounter:
    """Per-process counter for development and tests. Each server worker keeps its own counts."""

    _MAX_TRACKED_KEYS = 10_000

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._windows: dict[str, tuple[int, float]] = {}

    def hit(self, key: str, window_seconds: int) -> int:
        now = time.monotonic()
        with self._lock:
            count, expires_at = self._windows.get(key, (0, 0.0))
            if now >= expires_at:
                count, expires_at = 0, now + window_seconds
            self._windows[key] = (count + 1, expires_at)
            if len(self._windows) > self._MAX_TRACKED_KEYS:
                self._windows = {k: v for k, v in self._windows.items() if v[1] > now}
            return count + 1

    def clear(self) -> None:
        with self._lock:
            self._windows.clear()


class _RedisCounter:
    def __init__(self, url: str) -> None:
        self._client = redis.Redis.from_url(url, socket_timeout=1)

    def hit(self, key: str, window_seconds: int) -> int:
        bucket = f"ratelimit:{key}:{int(time.time() // window_seconds)}"
        pipeline = self._client.pipeline()
        pipeline.incr(bucket)
        pipeline.expire(bucket, window_seconds)
        count, _ = pipeline.execute()
        return int(count)


_redis_url = os.getenv("REDIS_URL")
_counter: _MemoryCounter | _RedisCounter = _RedisCounter(_redis_url) if _redis_url else _MemoryCounter()


def reset_rate_limits() -> None:
    """Clear in-process counts (used by tests)."""
    if isinstance(_counter, _MemoryCounter):
        _counter.clear()


def rate_limit(scope: str, limit: int, window_seconds: int = 60) -> Callable[[Request], None]:
    """FastAPI dependency allowing `limit` requests per client IP per window for the given scope."""

    def dependency(request: Request) -> None:
        client_ip = request.client.host if request.client else "unknown"
        try:
            count = _counter.hit(f"{scope}:{client_ip}", window_seconds)
        except redis.RedisError:
            # Fail open so a Redis outage doesn't take the whole API down; the error still reaches logs and Sentry.
            logger.exception("Rate limiter unavailable; allowing request")
            return
        if count > limit:
            raise HTTPException(
                status_code=429,
                detail="Too many requests. Please wait a moment and try again.",
                headers={"Retry-After": str(window_seconds)},
            )

    return dependency
