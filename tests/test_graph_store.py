"""KAN-23: Keystone's provenance store on a real Neo4j (skipped when Neo4j is not running)."""

from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest
import pytest_asyncio

from keystone.graph.engine import KeystoneGraph
from tests.neo4j_support import add_episode, open_test_graph, provenance


@pytest_asyncio.fixture
async def graph() -> AsyncIterator[KeystoneGraph]:
    async for g in open_test_graph():
        yield g


async def test_provenance_round_trip(graph: KeystoneGraph) -> None:
    p = provenance("doc1", author="Omar Siddiqui", pages="p. 2", url="file:///x.docx")
    episode = await add_episode(graph, p)
    stored = await graph.provenance([episode, "unknown-episode"])
    assert stored == {episode: p}
    assert stored[episode].observed_at == datetime(2026, 9, 15, tzinfo=UTC)


async def test_has_and_list_documents(graph: KeystoneGraph) -> None:
    assert not await graph.has_document("doc1")
    await add_episode(graph, provenance("doc1", "Overview"))
    await add_episode(graph, provenance("doc1", "Account team"))
    await add_episode(graph, provenance("doc2", function="Finance"))
    assert await graph.has_document("doc1")
    docs = {d.doc_id: d for d in await graph.list_documents()}
    assert (docs["doc1"].sections, docs["doc2"].business_function) == (2, "Finance")


async def test_delete_document_removes_only_that_document(graph: KeystoneGraph) -> None:
    await add_episode(graph, provenance("doc1"))
    keep = await add_episode(graph, provenance("doc2"))
    assert await graph.delete_document("doc1") == 1
    assert not await graph.has_document("doc1")
    assert await graph.has_document("doc2")
    assert list(await graph.provenance([keep])) == [keep]


async def test_setup_is_idempotent(graph: KeystoneGraph) -> None:
    await graph.setup()  # second run must not fail


async def test_add_section_rejects_unknown_business_function(graph: KeystoneGraph) -> None:
    with pytest.raises(ValueError, match="business_function must be one of"):
        await graph.add_section(provenance("doc1", function="Marketing"), "text")
