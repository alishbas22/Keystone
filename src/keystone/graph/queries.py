"""Scoped read queries over the knowledge graph (used by the MCP tools and the UI).

Every function takes `scope`: the business functions the caller may see. Filtering happens
inside the Cypher query, using the inherited tags from scopes.py, so out-of-scope data never
leaves the database ("filter before the agent sees it, never redact afterwards").
"""

from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

from graphiti_core.edges import EntityEdge

from keystone.graph.engine import KeystoneGraph
from keystone.graph.facts import build_facts, find_conflicts, load_decisions
from keystone.graph.provenance import Provenance
from keystone.graph.resolution import SAME_AS
from keystone.graph.scopes import EDGE_IN_SCOPE, NODE_IN_SCOPE, node_in_scope
from keystone.graph.truth import Conflict, Fact, classify

GRAPHITI_FIELDS = {
    "uuid", "name", "group_id", "labels", "created_at", "summary", "name_embedding",
}  # fmt: skip


@dataclass
class EntitySummary:
    uuid: str
    name: str
    entity_type: str
    aliases: list[str] = field(default_factory=list)
    summary: str = ""


@dataclass
class SourceView:
    """Where a fact came from (provenance), shown to agents and people."""

    document: str
    location: str
    source: str
    author: str | None
    observed_at: datetime
    business_function: str
    confidence: float
    source_type: str
    url: str | None


@dataclass
class FactView:
    id: str
    subject: str
    relation: str
    object: str
    text: str
    status: str
    since: datetime | None  # when the claim was made (newest source document)
    ended: datetime | None
    note: str
    sources: list[SourceView]
    conflicts_with: list[str]
    valid_from: datetime | None = None  # when it became true per the text, if stated


@dataclass
class EntityProfile:
    entity: EntitySummary
    business_functions: list[str]
    properties: dict[str, Any]
    current_facts: list[FactView]
    past_facts: list[FactView]  # history, superseded or disputed


@dataclass
class ConflictView:
    reason: str
    decided_by: str
    winner: str | None
    explanation: str
    facts: list[FactView]

    @property
    def open(self) -> bool:
        return self.winner is None


def _entity_type(labels: list[str], known: set[str]) -> str:
    return next((label for label in labels if label in known), "Entity")


def _source_view(p: Provenance) -> SourceView:
    return SourceView(
        document=p.file or p.title,
        location=p.location,
        source=p.source,
        author=p.author,
        observed_at=p.observed_at,
        business_function=p.business_function,
        confidence=p.confidence,
        source_type=p.source_type,
        url=p.url,
    )


def fact_view(fact: Fact, provenance: dict[str, Provenance], scope: list[str]) -> FactView:
    """A fact with ONLY the sources the caller may see (a fact can have sources in many scopes)."""
    sources = [
        _source_view(provenance[u])
        for u in fact.sources
        if u in provenance and provenance[u].business_function in scope
    ]
    return FactView(
        id=fact.id,
        subject=fact.subject,
        relation=fact.relation,
        object=fact.object,
        text=fact.text,
        status=str(fact.status),
        since=fact.observed_at,
        valid_from=fact.valid_from,
        ended=fact.ended_at,
        note=fact.note,
        sources=sorted(sources, key=lambda s: s.observed_at),
        conflicts_with=list(fact.conflicts_with),
    )


async def scoped_edges(
    graph: KeystoneGraph, where: str, scope: list[str], **params: Any
) -> list[EntityEdge]:
    rows = await graph.query(
        f"MATCH (a:Entity)-[e:RELATES_TO {{group_id: $group}}]->(b:Entity) "
        f"WHERE ({where}) AND {EDGE_IN_SCOPE} RETURN e.uuid AS uuid",
        group=graph.group,
        scope=scope,
        **params,
    )
    uuids = [str(r["uuid"]) for r in rows]
    return await EntityEdge.get_by_uuids(graph.graphiti.driver, uuids) if uuids else []


async def fact_views(
    graph: KeystoneGraph, edges: list[EntityEdge], scope: list[str]
) -> list[FactView]:
    facts, provenance = await build_facts(graph, edges)
    labelled = classify(facts, graph.ontology.rules, await load_decisions(graph))
    return [fact_view(f, provenance, scope) for f in labelled]


# --- Tools -------------------------------------------------------------------------------------


def _summary_allowed(graph: KeystoneGraph, scope: list[str]) -> bool:
    """Graphiti writes entity summaries from ALL documents, so a summary can mention facts from
    any business function. It cannot be filtered, so only callers with every function see it."""
    return set(graph.ontology.business_functions) <= set(scope)


async def search_entities(
    graph: KeystoneGraph, query: str, scope: list[str], limit: int = 10
) -> list[EntitySummary]:
    """Entities whose name or alias matches the query, best first."""
    known = set(graph.ontology.entity_types)
    show_summary = _summary_allowed(graph, scope)
    rows = await graph.query(
        f"MATCH (n:Entity {{group_id: $group}}) "
        f"WHERE {NODE_IN_SCOPE} AND (toLower(n.name) CONTAINS toLower($q) "
        f"  OR EXISTS {{ MATCH (n)-[:{SAME_AS}*1..5]-(m:Entity) "
        f"               WHERE toLower(m.name) CONTAINS toLower($q) }}) "
        f"OPTIONAL MATCH (n)-[:{SAME_AS}*1..5]-(alias:Entity) "
        f"WITH n, collect(DISTINCT alias.name) AS aliases, "
        f"  CASE WHEN toLower(n.name) = toLower($q) THEN 0 "
        f"       WHEN toLower(n.name) STARTS WITH toLower($q) THEN 1 "
        f"       WHEN toLower(n.name) CONTAINS toLower($q) THEN 2 ELSE 3 END AS rank "
        f"RETURN n.uuid AS uuid, n.name AS name, labels(n) AS labels, n.summary AS summary, "
        f"  aliases ORDER BY rank, size(n.name) LIMIT $limit",
        group=graph.group,
        scope=scope,
        q=query.strip(),
        limit=limit,
    )
    return [
        EntitySummary(
            uuid=str(r["uuid"]),
            name=str(r["name"]),
            entity_type=_entity_type([str(x) for x in r["labels"]], known),  # type: ignore[attr-defined]
            aliases=sorted(str(a) for a in r["aliases"]),  # type: ignore[attr-defined]
            summary=str(r["summary"] or "") if show_summary else "",
        )
        for r in rows
    ]


async def get_entity(graph: KeystoneGraph, uuid: str, scope: list[str]) -> EntityProfile | None:
    """Full profile: properties, aliases and current and past facts. None if not visible."""
    rows = await graph.query(
        f"MATCH (n:Entity {{uuid: $uuid, group_id: $group}}) WHERE {NODE_IN_SCOPE} "
        f"OPTIONAL MATCH (n)-[:{SAME_AS}*0..5]-(m:Entity) WHERE {node_in_scope('m')} "
        f"WITH n, collect(DISTINCT m) AS cluster "
        f"OPTIONAL MATCH (ep:Episodic)-[:MENTIONS]->(n) "
        f"OPTIONAL MATCH (ep)-[:HAS_PROVENANCE]->(s:KeystoneSource) "
        f"  WHERE s.business_function IN $scope "
        f"RETURN properties(n) AS props, labels(n) AS labels, "
        f"  [m IN cluster | m.uuid] AS cluster, "
        f"  [m IN cluster WHERE m.uuid <> n.uuid | m.name] AS aliases, "
        f"  collect(DISTINCT s.business_function) AS functions",
        uuid=uuid,
        group=graph.group,
        scope=scope,
    )
    if not rows:
        return None
    row = rows[0]
    props = dict(row["props"])  # type: ignore[call-overload]
    entity_type = _entity_type([str(x) for x in row["labels"]], set(graph.ontology.entity_types))  # type: ignore[attr-defined]
    cluster = [str(u) for u in row["cluster"]]  # type: ignore[attr-defined]
    edges = await scoped_edges(
        graph, "a.uuid IN $cluster OR b.uuid IN $cluster", scope, cluster=cluster
    )
    facts = await fact_views(graph, edges, scope)
    known_type = graph.ontology.entity_types.get(entity_type)
    allowed = set(known_type.properties) if known_type else set()  # ontology properties only
    return EntityProfile(
        entity=EntitySummary(
            uuid=uuid,
            name=str(props.get("name", "")),
            entity_type=entity_type,
            aliases=sorted(str(a) for a in row["aliases"]),  # type: ignore[attr-defined]
            summary=str(props.get("summary") or "") if _summary_allowed(graph, scope) else "",
        ),
        business_functions=sorted(str(f) for f in row["functions"]),  # type: ignore[attr-defined]
        properties={k: v for k, v in props.items() if k in allowed and v is not None},
        current_facts=[f for f in facts if f.status == "current"],
        past_facts=[f for f in facts if f.status != "current"],
    )


async def get_relationships(
    graph: KeystoneGraph, uuid: str, scope: list[str], relation: str | None = None
) -> list[FactView]:
    """Everything connected to an entity (and its merged duplicates), with status and sources."""
    profile = await get_entity(graph, uuid, scope)
    if profile is None:
        return []
    facts = profile.current_facts + profile.past_facts
    if relation:
        facts = [f for f in facts if f.relation == relation.lower()]
    return facts


async def find_path(
    graph: KeystoneGraph, from_uuid: str, to_uuid: str, scope: list[str], max_hops: int = 4
) -> list[FactView]:
    """The shortest chain of facts linking two entities, using only visible nodes and facts."""
    hops = max(1, min(int(max_hops), 6))
    rows = await graph.query(
        f"MATCH (a:Entity {{uuid: $a, group_id: $group}}), "
        f"      (b:Entity {{uuid: $b, group_id: $group}}) "
        f"MATCH p = shortestPath((a)-[:RELATES_TO*1..{hops}]-(b)) "
        f"WHERE all(e IN relationships(p) WHERE {EDGE_IN_SCOPE}) "
        f"  AND all(x IN nodes(p) WHERE {node_in_scope('x')}) "
        f"RETURN [e IN relationships(p) | e.uuid] AS edges",
        a=from_uuid,
        b=to_uuid,
        group=graph.group,
        scope=scope,
    )
    if not rows:
        return []
    order = [str(u) for u in rows[0]["edges"]]  # type: ignore[attr-defined]
    edges = await EntityEdge.get_by_uuids(graph.graphiti.driver, order)
    facts = {f.id: f for f in await fact_views(graph, edges, scope)}
    return [facts[u] for u in order if u in facts]


async def get_conflicts(
    graph: KeystoneGraph, scope: list[str], include_resolved: bool = True
) -> list[ConflictView]:
    """Contradictions between visible facts, with the rule that resolved them (if any)."""
    conflicts, facts, provenance = await find_conflicts(graph, scope)
    views = []
    for c in conflicts:
        if not include_resolved and not c.open:
            continue
        views.append(_conflict_view(c, facts, provenance, scope))
    return sorted(views, key=lambda v: (not v.open, v.reason))


def _conflict_view(
    c: Conflict, facts: dict[str, Fact], provenance: dict[str, Provenance], scope: list[str]
) -> ConflictView:
    return ConflictView(
        reason=c.reason,
        decided_by=str(c.decided_by),
        winner=c.winner,
        explanation=c.explanation,
        facts=[fact_view(facts[i], provenance, scope) for i in (c.a, c.b) if i in facts],
    )


async def explain_claim(graph: KeystoneGraph, fact_id: str, scope: list[str]) -> FactView | None:
    """A fact with every (visible) source: document, section, author, date, confidence."""
    edges = await scoped_edges(graph, "e.uuid = $fact_id", scope, fact_id=fact_id)
    if not edges:
        return None
    # Classify with the fact's contradicting neighbours so its status is correct.
    edge = edges[0]
    related = await scoped_edges(
        graph,
        "a.uuid IN $ends OR b.uuid IN $ends",
        scope,
        ends=[edge.source_node_uuid, edge.target_node_uuid],
    )
    views = await fact_views(graph, related or edges, scope)
    return next((v for v in views if v.id == fact_id), None)
