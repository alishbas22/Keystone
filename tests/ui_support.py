"""Run Streamlit pages headlessly against a seeded Neo4j test graph (no LLM calls)."""

import asyncio
import sys
import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path

import pytest
from streamlit.testing.v1 import AppTest

from keystone.config import Settings, get_settings
from keystone.graph.engine import KeystoneGraph, make_graphiti
from tests.graph_fixtures import Brightline, build_brightline

UI = Path(__file__).resolve().parents[1] / "ui"


def _graph(group: str) -> KeystoneGraph:
    settings = Settings(gemini_api_key="placeholder")  # type: ignore[arg-type]
    settings = settings.model_copy(update={"graph_group": group})
    return KeystoneGraph(make_graphiti(settings), settings)


async def _seed(group: str) -> Brightline:
    graph = _graph(group)
    try:
        await graph.graphiti.driver.health_check()
    except Exception:
        await graph.graphiti.close()  # type: ignore[no-untyped-call]
        pytest.skip("Neo4j is not reachable (start it with `docker compose up -d neo4j`)")
    try:
        await graph.setup()
        return await build_brightline(graph)
    finally:
        await graph.graphiti.close()  # type: ignore[no-untyped-call]


async def _cleanup(group: str) -> None:
    graph = _graph(group)
    try:
        await graph.delete_all()
    finally:
        await graph.graphiti.close()  # type: ignore[no-untyped-call]


@contextmanager
def seeded_ui(monkeypatch: pytest.MonkeyPatch) -> Iterator[Brightline]:
    """A test graph with the Brightline fixture; Streamlit pages read it via GRAPH_GROUP."""
    group = f"test-ui-{uuid.uuid4().hex[:8]}"
    data = asyncio.run(_seed(group))
    monkeypatch.setenv("GRAPH_GROUP", group)
    monkeypatch.setenv("GEMINI_API_KEY", "placeholder-no-llm-calls")
    monkeypatch.setenv("LLM_PROVIDER", "gemini")
    monkeypatch.syspath_prepend(str(UI))
    get_settings.cache_clear()
    try:
        yield data
    finally:
        asyncio.run(_cleanup(group))
        get_settings.cache_clear()
        sys.modules.pop("common", None)


def run_page(name: str, timeout: float = 60) -> AppTest:
    app = AppTest.from_file(str(UI / "views" / name), default_timeout=timeout)
    return app.run()
