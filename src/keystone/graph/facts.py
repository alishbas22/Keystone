"""Turn Graphiti edges into Keystone facts with names, dates, evidence and status."""

from datetime import UTC, datetime
from typing import Any

from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EntityNode

from keystone.graph.engine import KeystoneGraph
from keystone.graph.provenance import Provenance
from keystone.graph.resolution import canonical_names
from keystone.graph.truth import Conflict, Decisions, Evidence, Fact, classify, detect_conflicts

DECISION_LABEL = "KeystoneDecision"


def native(value: Any) -> datetime | None:
    """Neo4j DateTime -> Python datetime (None stays None)."""
    if value is None:
        return None
    return value.to_native() if hasattr(value, "to_native") else value  # type: ignore[no-any-return]


async def build_facts(
    graph: KeystoneGraph, edges: list[EntityEdge]
) -> tuple[list[Fact], dict[str, Provenance]]:
    """Facts for the given edges (not yet classified), plus provenance by episode uuid."""
    node_ids = list({u for e in edges for u in (e.source_node_uuid, e.target_node_uuid)})
    nodes = await EntityNode.get_by_uuids(graph.graphiti.driver, node_ids) if node_ids else []
    names = {n.uuid: n.name for n in nodes}
    # Entities merged by resolution share one name, so contradictions across them are found.
    canonical = await canonical_names(graph, node_ids) if node_ids else {}
    provenance = await graph.provenance(list({u for e in edges for u in e.episodes}))

    def name(uuid: str) -> str:
        return canonical.get(uuid) or names.get(uuid, "?")

    facts = []
    for edge in edges:
        sources = tuple(u for u in edge.episodes if u in provenance)
        evidence = tuple(
            Evidence(
                doc_id=provenance[u].doc_id,
                source_type=provenance[u].source_type,
                observed_at=provenance[u].observed_at,
                location=provenance[u].location,
            )
            for u in sources
        )
        dates = [e.observed_at for e in evidence if e.observed_at]
        facts.append(
            Fact(
                id=edge.uuid,
                subject=name(edge.source_node_uuid),
                relation=edge.name.lower(),
                object=name(edge.target_node_uuid),
                text=edge.fact,
                # When the fact became true (e.g. "effective 1 September"), else when written.
                observed_at=native(edge.valid_at) or (max(dates) if dates else None),
                ended_at=native(edge.invalid_at) or native(edge.expired_at),
                sources=sources,
                evidence=evidence,
            )
        )
    return facts, provenance


async def load_facts(
    graph: KeystoneGraph, edges: list[EntityEdge]
) -> tuple[list[Fact], dict[str, Provenance]]:
    """Facts for the given edges, labelled by the truth engine, plus provenance by episode."""
    facts, provenance = await build_facts(graph, edges)
    return classify(facts, graph.ontology.rules, await load_decisions(graph)), provenance


# --- Conflicts across the whole graph ------------------------------------------------------------


async def rule_edges(graph: KeystoneGraph, scope: list[str] | None = None) -> list[EntityEdge]:
    """Edges whose relation has a contradiction rule (opposites or single-valued).

    With `scope`, only facts visible to those business functions (filtered in the query).
    """
    from keystone.graph.scopes import EDGE_IN_SCOPE  # avoid an import cycle

    names = [r.upper() for r in graph.ontology.rules.relations_with_rules]
    in_scope = f" AND {EDGE_IN_SCOPE}" if scope is not None else ""
    rows = await graph.query(
        f"MATCH (:Entity)-[e:RELATES_TO {{group_id: $group}}]->(:Entity) "
        f"WHERE e.name IN $names{in_scope} RETURN e.uuid AS uuid",
        group=graph.group,
        names=names,
        scope=scope or [],
    )
    uuids = [str(r["uuid"]) for r in rows]
    return await EntityEdge.get_by_uuids(graph.graphiti.driver, uuids) if uuids else []


async def find_conflicts(
    graph: KeystoneGraph, scope: list[str] | None = None
) -> tuple[list[Conflict], dict[str, Fact], dict[str, Provenance]]:
    """Every contradiction (within scope) with its resolution, the facts involved and sources."""
    facts, provenance = await build_facts(graph, await rule_edges(graph, scope))
    decisions = await load_decisions(graph)
    conflicts = detect_conflicts(facts, graph.ontology.rules, decisions)
    involved = {fid for c in conflicts for fid in (c.a, c.b)}
    labelled = classify(facts, graph.ontology.rules, decisions)
    return conflicts, {f.id: f for f in labelled if f.id in involved}, provenance


# --- A person's decisions (conflict review) ------------------------------------------------------


async def record_decision(
    graph: KeystoneGraph, winner: str, loser: str, by: str, note: str = ""
) -> None:
    """Store a reviewer's choice between two contradicting facts. It overrides every rule."""
    await graph.query(
        f"MATCH (d:{DECISION_LABEL} {{group_id: $group}}) "
        f"WHERE (d.winner = $winner AND d.loser = $loser) "
        f"   OR (d.winner = $loser AND d.loser = $winner) "
        f"DELETE d",
        group=graph.group,
        winner=winner,
        loser=loser,
    )
    await graph.query(
        f"CREATE (:{DECISION_LABEL} {{group_id: $group, winner: $winner, loser: $loser, "
        f"decided_by: $by, note: $note, decided_at: $now}})",
        group=graph.group,
        winner=winner,
        loser=loser,
        by=by,
        note=note,
        now=datetime.now(UTC),
    )


async def clear_decision(graph: KeystoneGraph, a: str, b: str) -> None:
    await graph.query(
        f"MATCH (d:{DECISION_LABEL} {{group_id: $group}}) "
        f"WHERE (d.winner = $a AND d.loser = $b) OR (d.winner = $b AND d.loser = $a) DELETE d",
        group=graph.group,
        a=a,
        b=b,
    )


async def load_decisions(graph: KeystoneGraph) -> Decisions:
    rows = await graph.query(
        f"MATCH (d:{DECISION_LABEL} {{group_id: $group}}) RETURN d.winner AS w, d.loser AS l",
        group=graph.group,
    )
    return {frozenset((str(r["w"]), str(r["l"]))): str(r["w"]) for r in rows}
