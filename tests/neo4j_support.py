"""Shared helpers for tests that need a real Neo4j (skipped when none is reachable).

Tests use a unique graph group and delete it afterwards, so real data is never touched.
No LLM calls are made: a placeholder key only satisfies client construction.
"""

import uuid
from collections.abc import AsyncIterator
from datetime import UTC, datetime

import pytest

from keystone.config import Settings
from keystone.graph.engine import KeystoneGraph, make_graphiti
from keystone.graph.provenance import Provenance


async def open_test_graph() -> AsyncIterator[KeystoneGraph]:
    settings = Settings(gemini_api_key="placeholder-no-calls", llm_provider="gemini")  # type: ignore[arg-type]
    settings = settings.model_copy(update={"graph_group": f"test-{uuid.uuid4().hex[:8]}"})
    graphiti = make_graphiti(settings)
    try:
        await graphiti.driver.health_check()
    except Exception:
        await graphiti.close()  # type: ignore[no-untyped-call]
        pytest.skip("Neo4j is not reachable (start it with `docker compose up -d neo4j`)")
    graph = KeystoneGraph(graphiti, settings)
    await graph.setup()
    try:
        yield graph
    finally:
        await graph.delete_all()
        await graphiti.close()  # type: ignore[no-untyped-call]


async def add_episode(graph: KeystoneGraph, provenance: Provenance, content: str = "text") -> str:
    """Create a Graphiti episode node directly (no LLM) and attach provenance."""
    episode_uuid = str(uuid.uuid4())
    now = datetime.now(UTC)
    await graph.query(
        "CREATE (e:Episodic {uuid: $uuid, name: $name, group_id: $group, source: 'text', "
        "source_description: 'test', content: $content, created_at: $now, valid_at: $valid, "
        "entity_edges: []})",
        uuid=episode_uuid,
        name=provenance.title,
        group=graph.group,
        content=content,
        now=now,
        valid=provenance.observed_at,
    )
    await graph.record_provenance(episode_uuid, provenance)
    return episode_uuid


def provenance(
    doc_id: str, section: str = "Intro", function: str = "Sales", **extra: object
) -> Provenance:
    fields: dict[str, object] = {
        "doc_id": doc_id,
        "document_id": f"{doc_id}.docx",
        "source": "folder:test",
        "title": f"Doc {doc_id}",
        "file": f"{doc_id}.docx",
        "section": section,
        "observed_at": datetime(2026, 9, 15, tzinfo=UTC),
        "business_function": function,
        "author": "Test Author",
    }
    return Provenance.model_validate({**fields, **extra})
