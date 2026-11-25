"""KAN-27: MCP tools over a real Neo4j graph, called through an in-process MCP client."""

import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import pytest_asyncio
from mcp import Client

from keystone.graph.engine import KeystoneGraph
from keystone.mcp_server.server import create_server
from tests.graph_fixtures import Brightline, build_brightline
from tests.neo4j_support import open_test_graph


@pytest_asyncio.fixture
async def world() -> AsyncIterator[tuple[KeystoneGraph, Brightline]]:
    async for graph in open_test_graph():
        yield graph, await build_brightline(graph)


@asynccontextmanager
async def connect(
    world: tuple[KeystoneGraph, Brightline],
) -> AsyncIterator[tuple[Client, Brightline]]:
    """The client is opened inside the test's own task (anyio cancel scopes require it)."""
    graph, data = world
    async with Client(create_server(graph)) as client:
        yield client, data


async def call(client: Client, tool: str, **arguments: Any) -> dict[str, Any]:
    result = await client.call_tool(tool, arguments)
    assert not getattr(result, "is_error", False), result
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return dict(structured)
    return dict(json.loads(result.content[0].text))  # type: ignore[union-attr]


async def test_lists_the_tools(world: tuple[KeystoneGraph, Brightline]) -> None:
    async with connect(world) as (client, _):
        tools = {t.name for t in (await client.list_tools()).tools}
        assert tools == {
            "search_entities", "get_entity", "get_relationships", "find_path",
            "get_conflicts", "explain_claim", "describe_ontology",
        }  # fmt: skip


async def test_agent_workflow(world: tuple[KeystoneGraph, Brightline]) -> None:
    """Who is our customer's account manager? Search -> profile -> explain the source."""
    async with connect(world) as (client, data):
        found = await call(client, "search_entities", query="harbor")
        harbor = found["results"][0]
        assert (harbor["name"], harbor["entity_type"]) == ("Harbor Hotels Group", "Organization")

        profile = await call(client, "get_entity", uuid=harbor["uuid"])
        current = {
            f["subject"]: f for f in profile["current_facts"] if f["relation"] == "manages_account"
        }
        assert list(current) == ["Bilal Ahmed"]
        assert any(f["status"] == "superseded" for f in profile["past_facts"])

        explained = await call(client, "explain_claim", fact_id=current["Bilal Ahmed"]["id"])
        assert explained["sources"][0]["document"] == "memo.docx"
        assert explained["sources"][0]["observed_at"].startswith("2026-09-15")


async def test_conflicts_and_paths(world: tuple[KeystoneGraph, Brightline]) -> None:
    async with connect(world) as (client, data):
        conflicts = await call(client, "get_conflicts")
        assert conflicts["open"] == 0 and len(conflicts["conflicts"]) == 1
        assert conflicts["conflicts"][0]["decided_by"] == "time"
        path = await call(
            client,
            "find_path",
            from_uuid=data.entities["Omar Siddiqui"],
            to_uuid=data.entities["Brightline Furnishings"],
        )
        assert [f["relation"] for f in path["path"]] == [
            "reports_to",
            "manages_account",
            "customer_of",
        ]


async def test_describe_ontology(world: tuple[KeystoneGraph, Brightline]) -> None:
    async with connect(world) as (client, _):
        described = await call(client, "describe_ontology")
        assert "Organization" in described["entity_types"]
        assert described["relations"]["manages_account"]["from"] == ["Person"]
        assert ["likes", "dislikes"] in described["rules"]["opposites"]
        assert described["your_access"]["scope"] == ["Sales", "Finance", "HR", "Operations"]


async def test_unknown_entity(world: tuple[KeystoneGraph, Brightline]) -> None:
    async with connect(world) as (client, _):
        assert (await call(client, "get_entity", uuid="no-such-uuid"))["error"].startswith(
            "not found"
        )
