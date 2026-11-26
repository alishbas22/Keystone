"""KAN-36: the two-agent demo end to end over HTTP (Neo4j test graph; LLM extraction faked)."""

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio

from demo.two_agents import AGENTS, ensure_agents, run_demo, show_audit
from keystone.graph.engine import KeystoneGraph
from keystone.graph.provenance import Provenance
from keystone.mcp_server.auth import AgentRegistry
from tests.graph_fixtures import Brightline, build_brightline
from tests.http_support import mcp_http_server
from tests.neo4j_support import open_test_graph
from tests.test_claims import fake_extraction


@pytest_asyncio.fixture
async def world() -> AsyncIterator[tuple[KeystoneGraph, Brightline]]:
    async for graph in open_test_graph():
        data = await build_brightline(graph)
        stored: list[Provenance] = []
        graph.add_section = fake_extraction(graph, data, stored)  # type: ignore[method-assign]
        yield graph, data


async def test_two_agents_share_memory_but_not_scope(world: Any) -> None:
    graph, _ = world
    keys = await ensure_agents(graph)
    lines: list[str] = []
    claim = ("Harbor Hotels Group", "customer_of", "Brightline Furnishings", "Confirmed on a call.")
    async with mcp_http_server(graph) as url:
        seen = await run_demo(url, keys, claim, say=lines.append)
    await show_audit(graph, say=lines.append)

    # 1-2. Sales sees the account managers, the history and the rule that decided.
    assert seen["sales_managers"] == {"Bilal Ahmed": "current", "Sara Khan": "superseded"}
    assert seen["sales_conflicts"] == ["time"]
    # 3. Finance sees the customer's invoice but none of Sales' account manager facts.
    assert seen["finance_relations"] == ["billed_to"]
    assert seen["finance_sees_managers"] == 0
    # 4-5. A fact written by the Sales agent into the shared function is read by Finance.
    assert seen["claim_stored"] is True
    assert seen["finance_reads_claim"] == ["demo-sales"]
    # 6. Every call is audited per agent.
    audit = [line for line in lines if line.strip().startswith("demo-")]
    assert len(audit) == 2 and all("calls" in line for line in audit)


async def test_rerunning_rotates_keys(world: Any) -> None:
    graph, _ = world
    first = await ensure_agents(graph)
    second = await ensure_agents(graph)
    registry = AgentRegistry(graph)
    for name in AGENTS:
        assert first[name] != second[name]
        assert await registry.verify(first[name]) is None  # old key no longer works
        assert (await registry.verify(second[name])).scope == AGENTS[name]  # type: ignore[union-attr]
