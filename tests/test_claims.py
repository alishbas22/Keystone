"""KAN-28: agents write facts back, attributed and scoped (Neo4j; Graphiti's LLM step is faked)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
import pytest_asyncio
from graphiti_core.edges import EntityEdge

from keystone.graph import queries
from keystone.graph.claims import AGENT_CONFIDENCE, ClaimRejected, add_claim
from keystone.graph.engine import KeystoneGraph
from keystone.graph.provenance import Provenance
from tests.graph_fixtures import VECTOR, Brightline, build_brightline
from tests.neo4j_support import add_episode, open_test_graph


def fake_extraction(graph: KeystoneGraph, data: Brightline, stored: list[Provenance]) -> Any:
    """Stand-in for Graphiti's add_section: store the episode and the fact the LLM would extract."""

    async def add_section(provenance: Provenance, body: str, progress: Any = None) -> Any:
        stored.append(provenance)
        episode = await add_episode(graph, provenance, body)
        subject, obj = body.split(": ", 1)[1].split("\n")[0].rstrip(".").split(" -> ")
        relation = body.split()[2]
        edge = EntityEdge(
            source_node_uuid=data.entities[subject], target_node_uuid=data.entities[obj],
            name=relation, fact=f"{subject} {relation} {obj}", group_id=graph.group,
            episodes=[episode], created_at=provenance.observed_at,
            valid_at=provenance.observed_at, fact_embedding=VECTOR,
        )  # fmt: skip
        await edge.save(graph.graphiti.driver)
        return SimpleNamespace(episode=SimpleNamespace(uuid=episode), edges=[edge])

    return add_section


@pytest_asyncio.fixture
async def world() -> AsyncIterator[tuple[KeystoneGraph, Brightline, list[Provenance]]]:
    async for graph in open_test_graph():
        data = await build_brightline(graph)
        stored: list[Provenance] = []
        graph.add_section = fake_extraction(graph, data, stored)  # type: ignore[method-assign]
        yield graph, data, stored


async def test_claim_is_attributed_to_the_agent(world: Any) -> None:
    graph, _, stored = world
    when = datetime(2026, 11, 20, tzinfo=UTC)
    result = await add_claim(
        graph, "sales-bot", ["Sales"], "Sara Khan", "manages_account", "Harbor Hotels Group",
        details="Heard on the Nov 20 call.", observed_at=when,
    )  # fmt: skip
    [p] = stored
    assert (p.source, p.author, p.kind, p.confidence) == (
        "agent:sales-bot",
        "sales-bot",
        "agent",
        AGENT_CONFIDENCE,
    )
    assert (p.business_function, p.observed_at) == ("Sales", when)
    assert p.source_type == "agent"  # lowest source authority
    assert [f.relation for f in result.facts] == ["manages_account"]
    assert result.facts[0].sources[0].source == "agent:sales-bot"


async def test_contradictions_are_reported(world: Any) -> None:
    graph, data, _ = world
    result = await add_claim(
        graph, "sales-bot", ["Sales"], "Sara Khan", "manages_account", "Harbor Hotels Group",
        observed_at=datetime(2026, 11, 20, tzinfo=UTC),
    )  # fmt: skip
    assert [f.id for f in result.contradicts] == [data.facts["bilal_manages"]]


async def test_repeating_a_claim_is_not_corroboration(world: Any) -> None:
    graph, _, stored = world
    for _ in range(2):
        await add_claim(graph, "sales-bot", ["Sales"], "Bilal Ahmed", "knows", "Sara Khan")
    await add_claim(graph, "other-bot", ["Sales"], "Bilal Ahmed", "knows", "Sara Khan")
    ids = [p.doc_id for p in stored]
    assert ids[0] == ids[1] != ids[2]  # one source per agent, however often it repeats itself


async def test_another_agent_reads_what_was_written(world: Any) -> None:
    """Shared memory: Agent A writes, Agent B (same function) reads it, with attribution."""
    graph, data, _ = world
    await add_claim(graph, "agent-a", ["Sales"], "Bilal Ahmed", "knows", "Sara Khan")
    facts = await queries.get_relationships(graph, data.entities["Bilal Ahmed"], ["Sales"], "knows")
    assert [f.sources[0].author for f in facts] == ["agent-a"]
    assert (
        await queries.get_relationships(graph, data.entities["Bilal Ahmed"], ["HR"], "knows") == []
    )


async def test_agents_cannot_write_outside_their_scope(world: Any) -> None:
    graph, _, stored = world
    with pytest.raises(ClaimRejected, match="may only write to"):
        await add_claim(
            graph,
            "sales-bot",
            ["Sales"],
            "Sara Khan",
            "reports_to",
            "Omar Siddiqui",
            business_function="HR",
        )
    assert stored == []


async def test_relation_must_be_in_the_ontology(world: Any) -> None:
    graph, _, stored = world
    with pytest.raises(ClaimRejected, match="unknown relation 'is_friend_of'"):
        await add_claim(graph, "sales-bot", ["Sales"], "Sara Khan", "is_friend_of", "Bilal Ahmed")
    with pytest.raises(ClaimRejected, match="must not be empty"):
        await add_claim(graph, "sales-bot", ["Sales"], " ", "knows", "Bilal Ahmed")
    assert stored == []
