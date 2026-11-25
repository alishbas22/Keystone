"""KAN-30: every MCP tool call is audited (agent, tool, scope, time, returned entities)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio
from mcp import Client

from keystone.graph.engine import KeystoneGraph
from keystone.mcp_server.audit import read_log, returned_ids
from keystone.mcp_server.auth import AgentIdentity
from keystone.mcp_server.server import create_server
from tests.graph_fixtures import Brightline, build_brightline
from tests.neo4j_support import open_test_graph


@pytest_asyncio.fixture
async def world() -> AsyncIterator[tuple[KeystoneGraph, Brightline]]:
    async for graph in open_test_graph():
        yield graph, await build_brightline(graph)


def test_returned_ids_are_collected_at_any_depth() -> None:
    result = {
        "results": [{"uuid": "e1", "name": "A"}, {"uuid": "e2"}],
        "facts": [{"id": "f1", "sources": [{"document": "x"}], "conflicts_with": ["f2"]}],
    }
    assert returned_ids(result) == ["e1", "e2", "f1"]


async def test_tool_calls_are_logged(world: Any) -> None:
    graph, data = world
    sales = AgentIdentity("sales-bot", ["Sales"])
    async with Client(create_server(graph, agent=sales)) as client:
        await client.call_tool("search_entities", {"query": "harbor"})
        await client.call_tool("get_entity", {"uuid": data.entities["Omar Siddiqui"]})  # invisible

    log = await read_log(graph)
    assert [e.tool for e in log] == ["get_entity", "search_entities"]  # newest first
    search = log[1]
    assert (search.agent, search.scope, search.outcome) == ("sales-bot", ["Sales"], "ok")
    assert search.arguments == {"query": "harbor", "limit": 10}
    assert search.returned_ids == [data.entities["Harbor Hotels Group"]]
    hidden = log[0]
    assert hidden.returned_ids == [] and "not visible" in hidden.message


async def test_denied_calls_are_logged(world: Any) -> None:
    graph, _ = world
    async with Client(create_server(graph)) as client:  # no agent key
        result = await client.call_tool("describe_ontology", {})
        assert result.is_error
    [entry] = await read_log(graph)
    assert (entry.agent, entry.tool, entry.outcome) == ("unknown", "describe_ontology", "denied")


async def test_log_per_agent(world: Any) -> None:
    graph, _ = world
    for name, function in (("sales-bot", "Sales"), ("finance-bot", "Finance")):
        async with Client(create_server(graph, agent=AgentIdentity(name, [function]))) as client:
            await client.call_tool("search_entities", {"query": "a"})
    assert [e.agent for e in await read_log(graph, agent="finance-bot")] == ["finance-bot"]
    assert len(await read_log(graph)) == 2
