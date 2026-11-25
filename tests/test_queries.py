"""Scoped read queries (base of the MCP tools), on a small Brightline graph in Neo4j."""

from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from keystone.graph import queries
from keystone.graph.engine import KeystoneGraph
from keystone.graph.facts import record_decision
from keystone.graph.resolution import merge
from tests.graph_fixtures import Brightline, build_brightline
from tests.neo4j_support import open_test_graph

ALL = ["Sales", "Finance", "HR", "Operations"]


@pytest_asyncio.fixture
async def setup() -> AsyncIterator[tuple[KeystoneGraph, Brightline]]:
    async for graph in open_test_graph():
        yield graph, await build_brightline(graph)


@pytest.fixture
def graph(setup: tuple[KeystoneGraph, Brightline]) -> KeystoneGraph:
    return setup[0]


@pytest.fixture
def data(setup: tuple[KeystoneGraph, Brightline]) -> Brightline:
    return setup[1]


async def names(graph: KeystoneGraph, query: str, scope: list[str]) -> list[str]:
    return [e.name for e in await queries.search_entities(graph, query, scope)]


# --- search_entities ---------------------------------------------------------------------------


async def test_search_respects_scope(graph: KeystoneGraph) -> None:
    assert await names(graph, "khan", ["Sales"]) == ["Sara Khan"]
    assert await names(graph, "omar", ["Sales"]) == []  # only HR mentions Omar
    assert await names(graph, "omar", ["HR"]) == ["Omar Siddiqui"]
    assert await names(graph, "INV", ["Sales"]) == []
    assert await names(graph, "INV", ["Finance"]) == ["INV-2025-031"]


async def test_search_finds_aliases(graph: KeystoneGraph, data: Brightline) -> None:
    await merge(
        graph,
        data.entities["Harbor Hotels Group"],
        data.entities["Brightline Furnishings"],
        "test",
        1.0,
    )
    results = await queries.search_entities(graph, "brightline", ["Sales"])
    assert {r.name for r in results} == {"Brightline Furnishings", "Harbor Hotels Group"}


# --- get_entity / get_relationships -------------------------------------------------------------


async def test_entity_profile_with_current_and_past_facts(
    graph: KeystoneGraph, data: Brightline
) -> None:
    profile = await queries.get_entity(graph, data.entities["Harbor Hotels Group"], ["Sales"])
    assert profile is not None
    assert profile.entity.entity_type == "Organization"
    assert profile.business_functions == ["Sales"]  # Finance mentions it too, but not in scope
    current = {(f.subject, f.relation) for f in profile.current_facts}
    past = {(f.subject, f.relation, f.status) for f in profile.past_facts}
    assert current == {("Bilal Ahmed", "manages_account"), ("Harbor Hotels Group", "customer_of")}
    assert past == {("Sara Khan", "manages_account", "superseded")}


async def test_entity_outside_scope_is_invisible(graph: KeystoneGraph, data: Brightline) -> None:
    assert await queries.get_entity(graph, data.entities["Omar Siddiqui"], ["Sales"]) is None
    assert await queries.get_entity(graph, data.entities["Omar Siddiqui"], ["HR"]) is not None


async def test_facts_are_filtered_inside_a_visible_entity(
    graph: KeystoneGraph, data: Brightline
) -> None:
    """Sara is visible to Sales, but her HR reporting line is not."""
    sales = await queries.get_relationships(graph, data.entities["Sara Khan"], ["Sales"])
    hr = await queries.get_relationships(graph, data.entities["Sara Khan"], ["HR"])
    assert {f.relation for f in sales} == {"manages_account"}
    assert {f.relation for f in hr} == {"reports_to"}


async def test_relationship_filter(graph: KeystoneGraph, data: Brightline) -> None:
    facts = await queries.get_relationships(
        graph, data.entities["Harbor Hotels Group"], ALL, "billed_to"
    )
    assert [f.subject for f in facts] == ["INV-2025-031"]


# --- find_path ------------------------------------------------------------------------------------


async def test_find_path(graph: KeystoneGraph, data: Brightline) -> None:
    path = await queries.find_path(
        graph, data.entities["Sara Khan"], data.entities["Brightline Furnishings"], ["Sales"]
    )
    assert [f.relation for f in path] == ["manages_account", "customer_of"]


async def test_path_never_crosses_out_of_scope_facts(
    graph: KeystoneGraph, data: Brightline
) -> None:
    """Omar links to Harbor Hotels only through Sara's HR fact: invisible to Sales."""
    path = await queries.find_path(
        graph, data.entities["Omar Siddiqui"], data.entities["Harbor Hotels Group"], ["Sales"]
    )
    assert path == []
    full = await queries.find_path(
        graph, data.entities["Omar Siddiqui"], data.entities["Harbor Hotels Group"], ALL
    )
    assert [f.relation for f in full] == ["reports_to", "manages_account"]


# --- Regressions found on real data -------------------------------------------------------------


async def test_time_rule_uses_source_dates_not_graphiti_valid_at(
    graph: KeystoneGraph, data: Brightline
) -> None:
    """Graphiti falls back to the ingestion time for valid_at when the text has no date. The
    2025 account plan must still lose to the 2026 memo."""
    await graph.query(
        "MATCH ()-[e:RELATES_TO {uuid: $uuid}]->() SET e.valid_at = datetime()",
        uuid=data.facts["sara_manages"],
    )
    conflict = (await queries.get_conflicts(graph, ["Sales"]))[0]
    assert conflict.winner == data.facts["bilal_manages"]


async def test_summaries_do_not_leak_across_business_functions(
    graph: KeystoneGraph, data: Brightline
) -> None:
    """Graphiti summaries are written from all documents, so scoped callers never see or search them."""
    assert await names(graph, "summary", ["Finance"]) == []  # no matching through summaries
    sales = await queries.get_entity(graph, data.entities["Harbor Hotels Group"], ["Sales"])
    full = await queries.get_entity(graph, data.entities["Harbor Hotels Group"], ALL)
    assert sales is not None and sales.entity.summary == ""
    assert full is not None and full.entity.summary == "Harbor Hotels Group summary"


# --- get_conflicts / explain_claim -------------------------------------------------------------


async def test_conflicts(graph: KeystoneGraph, data: Brightline) -> None:
    conflicts = await queries.get_conflicts(graph, ["Sales"])
    assert len(conflicts) == 1
    conflict = conflicts[0]
    assert (conflict.decided_by, conflict.winner) == ("time", data.facts["bilal_manages"])
    assert {f.subject for f in conflict.facts} == {"Sara Khan", "Bilal Ahmed"}
    assert await queries.get_conflicts(graph, ["Finance"]) == []


async def test_reviewer_decision_changes_the_outcome(
    graph: KeystoneGraph, data: Brightline
) -> None:
    await record_decision(
        graph, data.facts["sara_manages"], data.facts["bilal_manages"], by="reviewer"
    )
    conflict = (await queries.get_conflicts(graph, ["Sales"]))[0]
    assert (conflict.decided_by, conflict.winner) == ("person", data.facts["sara_manages"])
    explained = await queries.explain_claim(graph, data.facts["bilal_manages"], ["Sales"])
    assert explained is not None and explained.status == "superseded"


async def test_explain_claim_shows_provenance(graph: KeystoneGraph, data: Brightline) -> None:
    fact = await queries.explain_claim(graph, data.facts["bilal_manages"], ["Sales"])
    assert fact is not None and fact.status == "current"
    [source] = fact.sources
    assert (source.document, source.business_function, source.author) == (
        "memo.docx",
        "Sales",
        "Test Author",
    )
    assert source.observed_at.year == 2026
    assert await queries.explain_claim(graph, data.facts["reports_to"], ["Sales"]) is None
