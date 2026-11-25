"""Business function tags (KAN-26): Sales, Finance, HR, Operations on every node and edge.

Tags are set at ingestion on each episode's provenance record (KeystoneSource.business_function)
and inherited through the graph:

- a fact (edge) carries the functions of the episodes it was extracted from;
- an entity (node) carries the functions of every episode that mentions it.

Inherited tags cannot go stale or be lost when Graphiti rewrites a node or edge, and the same
Cypher conditions below are used inside queries, so scoped access (KAN-29) filters results
in the database, before anything reaches an agent.
"""

from collections.abc import Iterable

from keystone.graph.engine import KeystoneGraph
from keystone.graph.provenance import PROVENANCE_REL, SOURCE_LABEL

# Cypher conditions. Use with a `$scope` parameter (list of business functions).
EDGE_IN_SCOPE = (
    f"any(ep IN e.episodes WHERE EXISTS {{ "
    f"MATCH (s:{SOURCE_LABEL} {{episode_uuid: ep}}) WHERE s.business_function IN $scope }})"
)


def node_in_scope(variable: str = "n") -> str:
    """Cypher condition: the node is mentioned by at least one episode in $scope."""
    return (
        f"EXISTS {{ MATCH (ep:Episodic)-[:MENTIONS]->({variable}) "
        f"MATCH (ep)-[:{PROVENANCE_REL}]->(s:{SOURCE_LABEL}) "
        f"WHERE s.business_function IN $scope }}"
    )


NODE_IN_SCOPE = node_in_scope("n")


async def edge_functions(graph: KeystoneGraph, edge_uuids: Iterable[str]) -> dict[str, set[str]]:
    """Business functions of each fact (edge), from the episodes it came from."""
    rows = await graph.query(
        f"MATCH ()-[e:RELATES_TO]->() WHERE e.uuid IN $uuids "
        f"OPTIONAL MATCH (s:{SOURCE_LABEL}) WHERE s.episode_uuid IN e.episodes "
        f"RETURN e.uuid AS uuid, collect(DISTINCT s.business_function) AS functions",
        uuids=list(edge_uuids),
    )
    return {str(r["uuid"]): {str(f) for f in r["functions"]} for r in rows}  # type: ignore[attr-defined]


async def node_functions(graph: KeystoneGraph, node_uuids: Iterable[str]) -> dict[str, set[str]]:
    """Business functions of each entity (node), from the episodes that mention it."""
    rows = await graph.query(
        f"MATCH (n:Entity) WHERE n.uuid IN $uuids "
        f"OPTIONAL MATCH (ep:Episodic)-[:MENTIONS]->(n) "
        f"OPTIONAL MATCH (ep)-[:{PROVENANCE_REL}]->(s:{SOURCE_LABEL}) "
        f"RETURN n.uuid AS uuid, collect(DISTINCT s.business_function) AS functions",
        uuids=list(node_uuids),
    )
    return {str(r["uuid"]): {str(f) for f in r["functions"]} for r in rows}  # type: ignore[attr-defined]


async def untagged(graph: KeystoneGraph) -> dict[str, list[str]]:
    """Nodes and edges with no business function (should be empty; shown as a health check)."""
    nodes = await graph.query(
        f"MATCH (n:Entity {{group_id: $group}}) WHERE NOT EXISTS {{ "
        f"MATCH (ep:Episodic)-[:MENTIONS]->(n) "
        f"MATCH (ep)-[:{PROVENANCE_REL}]->(:{SOURCE_LABEL}) }} "
        f"RETURN n.uuid AS uuid",
        group=graph.group,
    )
    edges = await graph.query(
        f"MATCH ()-[e:RELATES_TO {{group_id: $group}}]->() WHERE NOT any(ep IN e.episodes "
        f"WHERE EXISTS {{ MATCH (:{SOURCE_LABEL} {{episode_uuid: ep}}) }}) RETURN e.uuid AS uuid",
        group=graph.group,
    )
    return {"nodes": [str(r["uuid"]) for r in nodes], "edges": [str(r["uuid"]) for r in edges]}


async def function_summary(graph: KeystoneGraph) -> dict[str, dict[str, int]]:
    """How many entities and facts each business function can see (for the UI)."""
    summary = {}
    for function in graph.ontology.business_functions:
        nodes = await graph.query(
            f"MATCH (n:Entity {{group_id: $group}}) WHERE {NODE_IN_SCOPE} RETURN count(n) AS c",
            group=graph.group,
            scope=[function],
        )
        edges = await graph.query(
            f"MATCH ()-[e:RELATES_TO {{group_id: $group}}]->() WHERE {EDGE_IN_SCOPE} "
            f"RETURN count(e) AS c",
            group=graph.group,
            scope=[function],
        )
        summary[function] = {"entities": int(str(nodes[0]["c"])), "facts": int(str(edges[0]["c"]))}
    return summary
