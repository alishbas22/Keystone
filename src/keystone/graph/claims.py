"""Agents write facts back into the graph (KAN-28), attributed to the agent.

A claim is stored like any other source: one Graphiti episode with a Keystone provenance
record (source "agent:<name>", author = agent, kind "agent", lowest source authority). Two
agents sharing one Keystone therefore share memory, and every write can be traced and reviewed.
"""

import hashlib
from dataclasses import dataclass, field
from datetime import UTC, datetime

from keystone.graph.engine import KeystoneGraph
from keystone.graph.provenance import Provenance
from keystone.graph.queries import FactView, fact_views, scoped_edges

AGENT_CONFIDENCE = 0.7


class ClaimRejected(ValueError):
    """The claim breaks a rule (unknown relation, function outside the agent's scope...)."""


@dataclass
class ClaimResult:
    episode_uuid: str
    business_function: str
    facts: list[FactView] = field(default_factory=list)  # facts extracted from the claim
    contradicts: list[FactView] = field(default_factory=list)  # visible facts it conflicts with


def claim_text(graph: KeystoneGraph, subject: str, relation: str, obj: str, details: str) -> str:
    """A plain statement for extraction, e.g. 'Bilal Ahmed manages the account of X.'"""
    rel = graph.ontology.relation_types.get(relation)
    if rel is None:
        known = ", ".join(sorted(graph.ontology.relation_types))
        raise ClaimRejected(f"unknown relation '{relation}'. Use one of: {known}")
    statement = f"Fact type {relation.upper()} ({rel.description}): {subject} -> {obj}."
    return f"{statement}\n{details.strip()}" if details.strip() else statement


async def add_claim(
    graph: KeystoneGraph,
    agent_name: str,
    agent_scope: list[str],
    subject: str,
    relation: str,
    obj: str,
    details: str = "",
    business_function: str | None = None,
    observed_at: datetime | None = None,
) -> ClaimResult:
    """Store an agent's claim and return the facts it produced (with any contradictions)."""
    if not subject.strip() or not obj.strip():
        raise ClaimRejected("subject and object must not be empty")
    function = business_function or (agent_scope[0] if agent_scope else "")
    if function not in agent_scope:
        raise ClaimRejected(
            f"agent '{agent_name}' may only write to {agent_scope}, not '{function}'"
        )
    body = claim_text(graph, subject.strip(), relation, obj.strip(), details)
    when = observed_at or datetime.now(UTC)
    # Same agent + same claim = same id: repeating a claim is not independent corroboration.
    claim_key = f"{agent_name}|{subject.strip().lower()}|{relation}|{obj.strip().lower()}"
    provenance = Provenance(
        doc_id=hashlib.sha256(claim_key.encode()).hexdigest()[:16],
        document_id=f"claim/{when.isoformat()}",
        source=f"agent:{agent_name}",
        title=f"Claim by {agent_name}",
        section=relation,
        author=agent_name,
        observed_at=when,
        business_function=function,
        confidence=AGENT_CONFIDENCE,
        kind="agent",
    )
    result = await graph.add_section(provenance, body)
    new_ids = {e.uuid for e in result.edges}
    if not new_ids:
        return ClaimResult(result.episode.uuid, function)
    # Classify the new facts together with the agent's visible neighbours, so contradictions
    # with existing facts are reported, without revealing anything outside the agent's scope.
    ends = list({u for e in result.edges for u in (e.source_node_uuid, e.target_node_uuid)})
    related = await scoped_edges(
        graph, "a.uuid IN $ends OR b.uuid IN $ends", agent_scope, ends=ends
    )
    views = {v.id: v for v in await fact_views(graph, related, agent_scope)}
    facts = [views[i] for i in new_ids if i in views]
    conflicting = {c for f in facts for c in f.conflicts_with if c not in new_ids}
    return ClaimResult(
        result.episode.uuid, function, facts, [views[c] for c in conflicting if c in views]
    )
