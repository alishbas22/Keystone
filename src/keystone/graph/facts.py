"""Turn Graphiti edges into Keystone facts with names, dates and provenance (KAN-23)."""

from datetime import datetime
from typing import Any

from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EntityNode

from keystone.graph.engine import KeystoneGraph
from keystone.graph.provenance import Provenance
from keystone.graph.truth import Fact, classify


def native(value: Any) -> datetime | None:
    """Neo4j DateTime -> Python datetime (None stays None)."""
    if value is None:
        return None
    return value.to_native() if hasattr(value, "to_native") else value  # type: ignore[no-any-return]


async def load_facts(
    graph: KeystoneGraph, edges: list[EntityEdge]
) -> tuple[list[Fact], dict[str, Provenance]]:
    """Facts for the given edges, labelled by the truth engine, plus provenance by episode."""
    node_ids = list({u for e in edges for u in (e.source_node_uuid, e.target_node_uuid)})
    nodes = await EntityNode.get_by_uuids(graph.graphiti.driver, node_ids) if node_ids else []
    names = {n.uuid: n.name for n in nodes}
    provenance = await graph.provenance(list({u for e in edges for u in e.episodes}))

    facts = []
    for edge in edges:
        sources = tuple(u for u in edge.episodes if u in provenance)
        dates = [provenance[u].observed_at for u in sources]
        facts.append(
            Fact(
                id=edge.uuid,
                subject=names.get(edge.source_node_uuid, "?"),
                relation=edge.name.lower(),
                object=names.get(edge.target_node_uuid, "?"),
                text=edge.fact,
                observed_at=native(edge.valid_at) or (max(dates) if dates else None),
                ended_at=native(edge.invalid_at) or native(edge.expired_at),
                sources=sources,
            )
        )
    return classify(facts, graph.ontology.rules), provenance
