"""KAN-26: business function tags, inherited from provenance (real Neo4j, no LLM)."""

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio

from keystone.graph.engine import KeystoneGraph
from keystone.graph.scopes import (
    EDGE_IN_SCOPE,
    NODE_IN_SCOPE,
    edge_functions,
    function_summary,
    node_functions,
    untagged,
)
from tests.neo4j_support import add_episode, open_test_graph, provenance


@pytest_asyncio.fixture
async def graph() -> AsyncIterator[KeystoneGraph]:
    async for g in open_test_graph():
        yield g


async def entity(graph: KeystoneGraph, name: str, mentioned_by: list[str]) -> str:
    uid = str(uuid.uuid4())
    await graph.query(
        "CREATE (n:Entity:Organization {uuid: $uid, name: $name, group_id: $group}) "
        "WITH n UNWIND $episodes AS ep MATCH (e:Episodic {uuid: ep}) CREATE (e)-[:MENTIONS]->(n)",
        uid=uid,
        name=name,
        group=graph.group,
        episodes=mentioned_by,
    )
    return uid


async def fact(graph: KeystoneGraph, a: str, b: str, episodes: list[str]) -> str:
    uid = str(uuid.uuid4())
    await graph.query(
        "MATCH (a:Entity {uuid: $a}), (b:Entity {uuid: $b}) "
        "CREATE (a)-[:RELATES_TO {uuid: $uid, name: 'CUSTOMER_OF', fact: 'x', group_id: $group, "
        "episodes: $episodes}]->(b)",
        a=a,
        b=b,
        uid=uid,
        group=graph.group,
        episodes=episodes,
    )
    return uid


async def test_tags_are_inherited_from_sources(graph: KeystoneGraph) -> None:
    sales = await add_episode(graph, provenance("plan", function="Sales"))
    finance = await add_episode(graph, provenance("invoice", function="Finance"))
    harbor = await entity(graph, "Harbor Hotels Group", [sales, finance])
    brightline = await entity(graph, "Brightline", [sales])
    customer_of = await fact(graph, harbor, brightline, [sales])

    assert await node_functions(graph, [harbor, brightline]) == {
        harbor: {"Sales", "Finance"},  # mentioned in both
        brightline: {"Sales"},
    }
    assert await edge_functions(graph, [customer_of]) == {customer_of: {"Sales"}}
    assert await untagged(graph) == {"nodes": [], "edges": []}


async def test_scope_conditions_filter_inside_the_query(graph: KeystoneGraph) -> None:
    sales = await add_episode(graph, provenance("plan", function="Sales"))
    hr = await add_episode(graph, provenance("review", function="HR"))
    harbor = await entity(graph, "Harbor Hotels Group", [sales])
    salary = await entity(graph, "Salary band 4", [hr])
    await fact(graph, harbor, salary, [hr])

    async def visible_nodes(scope: list[str]) -> set[str]:
        rows = await graph.query(
            f"MATCH (n:Entity {{group_id: $group}}) WHERE {NODE_IN_SCOPE} RETURN n.name AS name",
            group=graph.group,
            scope=scope,
        )
        return {str(r["name"]) for r in rows}

    async def visible_facts(scope: list[str]) -> int:
        rows = await graph.query(
            f"MATCH ()-[e:RELATES_TO {{group_id: $group}}]->() WHERE {EDGE_IN_SCOPE} RETURN count(e) AS c",
            group=graph.group,
            scope=scope,
        )
        return int(str(rows[0]["c"]))

    assert await visible_nodes(["Sales"]) == {"Harbor Hotels Group"}
    assert await visible_nodes(["HR"]) == {"Salary band 4"}
    assert await visible_nodes(["Sales", "HR"]) == {"Harbor Hotels Group", "Salary band 4"}
    assert await visible_nodes(["Finance"]) == set()
    assert (await visible_facts(["Sales"]), await visible_facts(["HR"])) == (0, 1)
    summary = await function_summary(graph)
    assert summary["Sales"] == {"entities": 1, "facts": 0}
    assert summary["HR"] == {"entities": 1, "facts": 1}


async def test_untagged_items_are_reported(graph: KeystoneGraph) -> None:
    orphan = await entity(graph, "Mystery Ltd", [])
    assert (await untagged(graph))["nodes"] == [orphan]
