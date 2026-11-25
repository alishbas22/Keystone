"""KAN-24: entity resolution (duplicate detection and reversible merges)."""

import uuid
from collections.abc import AsyncIterator

import pytest_asyncio

from keystone.graph.engine import KeystoneGraph
from keystone.graph.resolution import (
    EntityRecord,
    canonical_names,
    find_candidates,
    load_entities,
    merge,
    normalise,
    resolve_duplicates,
    same_as,
    score_pair,
    unmerge,
)
from tests.neo4j_support import open_test_graph


def entity(name: str, entity_type: str = "Organization", **attributes: str) -> EntityRecord:
    return EntityRecord(str(uuid.uuid4()), name, entity_type, dict(attributes))


# --- Scoring (no database) -----------------------------------------------------------------------


def test_normalise() -> None:
    assert normalise("HARBOR HOTELS GROUP LTD.") == "harbor hotels group"
    assert normalise("Smith & Sons, Inc.") == "smith and sons"
    assert normalise("Café Nord GmbH") == "cafe nord"


def test_same_name_after_normalising() -> None:
    score, reason = score_pair(entity("Harbor Hotels Group Ltd"), entity("harbor hotels group"), [])
    assert score >= 0.97 and reason == "same name after normalising"


def test_identity_property_decides() -> None:
    a = entity("Ergo Chair", "Product", sku="BF-CH-100")
    b = entity("Ergonomic Desk Chair", "Product", sku="bf-ch-100")
    c = entity("Ergo Chair", "Product", sku="BF-CH-200")
    assert score_pair(a, b, ["sku"]) == (1.0, "same sku")
    assert score_pair(a, c, ["sku"]) == (0.0, "different sku")  # same name, different SKU


def test_initials() -> None:
    score, reason = score_pair(entity("S. Khan", "Person"), entity("Sara Khan", "Person"), [])
    assert (score, reason) == (0.9, "initials match")
    assert score_pair(entity("S. Khan", "Person"), entity("Sara Ahmed", "Person"), [])[0] < 0.85


def test_contained_names_need_whole_words() -> None:
    assert score_pair(entity("Harbor Hotels"), entity("Harbor Hotels Group"), [])[0] >= 0.85
    assert score_pair(entity("Acme"), entity("Acme Logistics Partners"), [])[0] < 0.85
    assert score_pair(entity("Crestview Bank"), entity("Crestview Banking Partners"), [])[0] < 0.85


def test_find_candidates_compares_same_type_only() -> None:
    people = [entity("Sara Khan", "Person"), entity("S. Khan", "Person")]
    orgs = [entity("Sara Khan", "Organization"), entity("Crestview Bank")]
    candidates = find_candidates(people + orgs, {"Person": ["email"]})
    assert [(c.a.name, c.b.name, c.auto) for c in candidates] == [("Sara Khan", "S. Khan", False)]


def test_only_near_certain_pairs_are_automatic() -> None:
    candidates = find_candidates(
        [entity("Harbor Hotels Group Ltd"), entity("Harbor Hotels Group"), entity("Harbor Hotels")],
        {},
    )
    auto = [(c.a.name, c.b.name) for c in candidates if c.auto]
    assert auto == [("Harbor Hotels Group Ltd", "Harbor Hotels Group")]
    assert len(candidates) == 3  # the others are offered for review


# --- Reversible merges on Neo4j --------------------------------------------------------------------


@pytest_asyncio.fixture
async def graph() -> AsyncIterator[KeystoneGraph]:
    async for g in open_test_graph():
        yield g


async def add_entity(
    graph: KeystoneGraph, name: str, label: str = "Organization", **props: str
) -> str:
    uid = str(uuid.uuid4())
    await graph.query(
        f"CREATE (n:Entity:{label} {{uuid: $uid, name: $name, group_id: $group}}) SET n += $props",
        uid=uid,
        name=name,
        group=graph.group,
        props=props,
    )
    return uid


async def test_merge_and_unmerge(graph: KeystoneGraph) -> None:
    a = await add_entity(graph, "Harbor Hotels")
    b = await add_entity(graph, "Harbor Hotels Group")
    c = await add_entity(graph, "HHG")
    await merge(graph, a, b, "similar names", 0.9, by="reviewer@test")
    await merge(graph, b, c, "acronym", 0.9, by="reviewer@test")
    assert await same_as(graph, a) == sorted([a, b, c])  # merges chain together
    names = await canonical_names(graph, [a, b, c])
    assert set(names.values()) == {"Harbor Hotels Group"}

    await unmerge(graph, b, c)
    assert await same_as(graph, a) == sorted([a, b])
    assert await same_as(graph, c) == [c]
    assert len(await load_entities(graph)) == 3  # nothing was ever deleted


async def test_resolve_duplicates_auto_merges_only_certain_pairs(graph: KeystoneGraph) -> None:
    a = await add_entity(graph, "Crestview Bank Ltd")
    b = await add_entity(graph, "Crestview Bank")
    c = await add_entity(graph, "Crestview Banking Partners")
    p1 = await add_entity(graph, "Ergo Chair", "Product", sku="BF-CH-100")
    p2 = await add_entity(graph, "Ergonomic Desk Chair", "Product", sku="BF-CH-100")
    candidates = await resolve_duplicates(graph)
    assert {frozenset((c.a.name, c.b.name)) for c in candidates if c.auto} == {
        frozenset(("Crestview Bank Ltd", "Crestview Bank")),
        frozenset(("Ergo Chair", "Ergonomic Desk Chair")),
    }
    assert await same_as(graph, a) == sorted([a, b])
    assert await same_as(graph, c) == [c]
    assert await same_as(graph, p1) == sorted([p1, p2])
