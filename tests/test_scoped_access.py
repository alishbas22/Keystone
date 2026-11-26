"""KAN-29: agent keys tied to business functions, enforced by the MCP server.

Includes an end-to-end test over real HTTP with bearer keys: a Sales agent and a Finance agent
query the same server and each sees only its own business function's data.
"""

import asyncio
import json
import sys
from collections.abc import AsyncIterator
from typing import Any

import pytest
import pytest_asyncio
from mcp import Client
from mcp.client.streamable_http import streamable_http_client
from mcp.shared._httpx_utils import create_mcp_http_client

from keystone.graph.engine import KeystoneGraph
from keystone.mcp_server.auth import (
    AGENT_LABEL,
    AgentIdentity,
    AgentRegistry,
    KeystoneTokenVerifier,
)
from keystone.mcp_server.server import create_server
from tests.graph_fixtures import Brightline, build_brightline
from tests.http_support import mcp_http_server
from tests.neo4j_support import open_test_graph


@pytest_asyncio.fixture
async def world() -> AsyncIterator[tuple[KeystoneGraph, Brightline]]:
    async for graph in open_test_graph():
        yield graph, await build_brightline(graph)


def result_json(result: Any) -> dict[str, Any]:
    structured = getattr(result, "structured_content", None)
    return dict(structured) if structured is not None else dict(json.loads(result.content[0].text))


# --- The registry -----------------------------------------------------------------------------


async def test_keys_are_hashed_and_shown_once(world: Any) -> None:
    graph, _ = world
    registry = AgentRegistry(graph)
    agent, key = await registry.create("sales-bot", ["Sales"])
    assert key.startswith("ks_") and len(key) > 40
    stored = await graph.query(
        f"MATCH (a:{AGENT_LABEL} {{group_id: $g}}) RETURN properties(a) AS p", g=graph.group
    )
    assert key not in str(stored)  # only the hash is stored
    assert await registry.verify(key) == AgentIdentity("sales-bot", ["Sales"])
    assert [a.key_hint for a in await registry.list_agents()] == [key[:7]]


async def test_wrong_and_revoked_keys_get_nothing(world: Any) -> None:
    graph, _ = world
    registry = AgentRegistry(graph)
    _, key = await registry.create("sales-bot", ["Sales"])
    assert await registry.verify("ks_wrong") is None
    assert await registry.verify("not-even-a-key") is None
    await registry.revoke("sales-bot")
    assert await registry.verify(key) is None
    assert [a.active for a in await registry.list_agents()] == [False]


async def test_changing_an_agents_functions(world: Any) -> None:
    graph, _ = world
    registry = AgentRegistry(graph)
    _, key = await registry.create("ops", ["Operations"])
    await registry.set_functions("ops", ["Sales", "Operations"])
    assert (await registry.verify(key)).scope == ["Sales", "Operations"]  # type: ignore[union-attr]


async def test_registry_validation(world: Any) -> None:
    graph, _ = world
    registry = AgentRegistry(graph)
    await registry.create("sales-bot", ["Sales"])
    with pytest.raises(ValueError, match="already exists"):
        await registry.create("sales-bot", ["Sales"])
    with pytest.raises(ValueError, match="non-empty subset"):
        await registry.create("x-bot", ["Marketing"])
    with pytest.raises(ValueError, match="lowercase"):
        await registry.create("Sales Bot", ["Sales"])


async def test_token_verifier(world: Any) -> None:
    graph, _ = world
    registry = AgentRegistry(graph)
    _, key = await registry.create("finance-bot", ["Finance"])
    token = await KeystoneTokenVerifier(registry).verify_token(key)
    assert token is not None and (token.client_id, token.scopes) == ("finance-bot", ["Finance"])
    assert await KeystoneTokenVerifier(registry).verify_token("ks_nope") is None


# --- Enforcement in the MCP tools ------------------------------------------------------------------


async def test_server_without_an_agent_denies_everything(world: Any) -> None:
    graph, _ = world
    async with Client(create_server(graph)) as client:
        result = await client.call_tool("search_entities", {"query": "harbor"})
        assert result.is_error
        assert "Access denied: no agent key" in result.content[0].text  # type: ignore[union-attr]


async def test_agents_see_only_their_business_functions(world: Any) -> None:
    graph, data = world
    sales = AgentIdentity("sales-bot", ["Sales"])
    finance = AgentIdentity("finance-bot", ["Finance"])
    async with Client(create_server(graph, agent=sales)) as client:
        found = result_json(await client.call_tool("search_entities", {"query": "INV"}))
        assert found["results"] == []
        omar = result_json(
            await client.call_tool("get_entity", {"uuid": data.entities["Omar Siddiqui"]})
        )
        assert "error" in omar
    async with Client(create_server(graph, agent=finance)) as client:
        found = result_json(await client.call_tool("search_entities", {"query": "INV"}))
        assert [r["name"] for r in found["results"]] == ["INV-2025-031"]
        conflicts = result_json(await client.call_tool("get_conflicts", {}))
        assert conflicts["conflicts"] == []  # the account manager dispute is Sales data


# --- End to end over HTTP with bearer keys ------------------------------------------------------


@pytest.mark.parametrize(
    "mode",
    [
        "legacy",  # initialize handshake: what most MCP clients use today
        pytest.param(
            "auto",  # newest protocol (2026-07-28) with server/discover
            marks=pytest.mark.skipif(
                sys.platform == "win32",
                reason="on the dev Windows machine the newest-protocol HTTP response body is cut "
                "off before it reaches the client (antivirus HTTP inspection suspected); "
                "covered on Linux CI",
            ),
        ),
    ],
)
async def test_http_bearer_keys_end_to_end(world: Any, mode: str) -> None:
    graph, _ = world
    registry = AgentRegistry(graph)
    _, sales_key = await registry.create("sales-bot", ["Sales"])
    _, finance_key = await registry.create("finance-bot", ["Finance"])

    async with mcp_http_server(graph) as url:

        async def search_as(key: str, query: str) -> list[str]:
            http = create_mcp_http_client(headers={"Authorization": f"Bearer {key}"})
            async with Client(streamable_http_client(url, http_client=http), mode=mode) as client:
                found = result_json(await client.call_tool("search_entities", {"query": query}))
                return [r["name"] for r in found["results"]]

        assert await search_as(sales_key, "harbor") == ["Harbor Hotels Group"]
        assert await search_as(sales_key, "INV") == []
        assert await search_as(finance_key, "INV") == ["INV-2025-031"]
        with pytest.raises(Exception):  # noqa: B017  # unknown key: the HTTP layer refuses (401)
            await asyncio.wait_for(search_as("ks_not-a-real-key", "harbor"), timeout=30)
