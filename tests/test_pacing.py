"""Pacing LLM calls under provider rate limits (no network)."""

import asyncio
import time
from typing import Any

import pytest
from graphiti_core.llm_client.errors import RateLimitError

from keystone.config import Settings
from keystone.graph.engine import make_graphiti
from keystone.graph.pacing import RequestPacer, pace


async def test_pacer_spaces_calls_evenly() -> None:
    pacer = RequestPacer(per_minute=600)  # one call every 0.1 s
    start = time.monotonic()
    for _ in range(4):
        await pacer.wait()
    assert time.monotonic() - start >= 0.29  # first call immediate, then 3 x 0.1 s


async def test_pacer_spaces_concurrent_callers() -> None:
    pacer = RequestPacer(per_minute=600)
    start = time.monotonic()
    await asyncio.gather(*(pacer.wait() for _ in range(4)))
    assert time.monotonic() - start >= 0.29


async def test_unlimited_pacer_never_waits() -> None:
    pacer = RequestPacer(per_minute=0)
    assert [await pacer.wait() for _ in range(5)] == [0.0] * 5


class Flaky:
    def __init__(self, failures: int, error: Exception) -> None:
        self.failures, self.error, self.calls = failures, error, 0

    async def generate_response(self, prompt: str) -> str:
        self.calls += 1
        if self.calls <= self.failures:
            raise self.error
        return f"answer to {prompt}"


async def test_single_calls_are_retried_on_rate_limits() -> None:
    client = Flaky(failures=2, error=RateLimitError())
    pace(client, "generate_response", RequestPacer(0), retries=3, base_wait=0)
    assert await client.generate_response("q") == "answer to q"
    assert client.calls == 3


async def test_other_errors_are_not_retried() -> None:
    client = Flaky(failures=1, error=ValueError("bad schema"))
    pace(client, "generate_response", RequestPacer(0), retries=3, base_wait=0)
    with pytest.raises(ValueError):
        await client.generate_response("q")
    assert client.calls == 1


def test_graphiti_clients_are_paced() -> None:
    graphiti = make_graphiti(Settings(gemini_api_key="test-key"))  # type: ignore[arg-type]
    clients: list[tuple[Any, str]] = [
        (graphiti.llm_client, "generate_response"),
        (graphiti.cross_encoder, "rank"),
        (graphiti.embedder, "create"),
    ]
    for client, method in clients:
        assert hasattr(getattr(client, method), "__wrapped__"), method
