"""Pace LLM calls to stay within provider rate limits, and retry single calls on temporary errors.

Free tiers allow few requests per minute (Gemini flash-lite: about 15), while Graphiti needs many
calls per section. Retrying a whole section would need all of those calls again within one minute
and could never succeed, so instead every individual call is spaced out (`RequestPacer`) and a
call that still hits a limit is retried on its own after a wait (`pace`).
"""

import asyncio
import functools
import time
from collections.abc import Awaitable, Callable
from typing import Any

from graphiti_core.llm_client.errors import RateLimitError

_TRANSIENT_MARKERS = {
    "429": "Rate limit reached",
    "resource_exhausted": "Rate limit reached",
    "503": "Provider busy",
    "unavailable": "Provider busy",
    "high demand": "Provider busy",
    "overloaded": "Provider busy",
    "timed out": "Provider timeout",
    "timeout": "Provider timeout",
}


def transient_problem(exc: BaseException) -> str | None:
    """A short label if the error is temporary (rate limit, overload, timeout), else None.

    Graphiti wraps provider errors, so the whole cause chain is checked.
    """
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, RateLimitError):
            return "Rate limit reached"
        text = str(seen).lower()
        for marker, label in _TRANSIENT_MARKERS.items():
            if marker in text:
                return label
        seen = seen.__cause__ or seen.__context__
    return None


class RequestPacer:
    """Allows at most `per_minute` calls per minute, evenly spaced. 0 means unlimited."""

    def __init__(self, per_minute: float = 0, clock: Callable[[], float] = time.monotonic):
        self.interval = 60.0 / per_minute if per_minute > 0 else 0.0
        self._clock = clock
        self._next = 0.0
        self._lock = asyncio.Lock()

    async def wait(self) -> float:
        """Wait for the next free slot. Returns the seconds waited."""
        if not self.interval:
            return 0.0
        async with self._lock:
            now = self._clock()
            start = max(now, self._next)
            self._next = start + self.interval
        delay = start - now
        if delay > 0:
            await asyncio.sleep(delay)
        return delay


def pace(
    target: Any,
    method: str,
    pacer: RequestPacer,
    retries: int,
    base_wait: float,
) -> None:
    """Replace `target.method` (async) with a paced version that retries temporary errors."""
    original: Callable[..., Awaitable[Any]] = getattr(target, method)

    @functools.wraps(original)
    async def paced(*args: Any, **kwargs: Any) -> Any:
        attempt = 0
        while True:
            await pacer.wait()
            try:
                return await original(*args, **kwargs)
            except Exception as exc:
                attempt += 1
                if transient_problem(exc) is None or attempt > retries:
                    raise
                await asyncio.sleep(base_wait * 2 ** (attempt - 1))

    setattr(target, method, paced)
