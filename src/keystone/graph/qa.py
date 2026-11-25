"""Answer a question from the knowledge graph, with sources (minimal demo version).

1. Graphiti hybrid search finds the most relevant facts (edges).
2. Each fact is linked back to the episodes (document sections) it came from.
3. The truth engine labels facts as current / history / superseded / disputed.
4. The LLM writes an answer using ONLY those numbered facts and cites them.
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Any

from graphiti_core import Graphiti
from graphiti_core.edges import EntityEdge
from graphiti_core.nodes import EntityNode, EpisodicNode
from graphiti_core.prompts.models import Message
from pydantic import BaseModel, Field

from keystone.config import get_settings
from keystone.graph.engine import SourceRef
from keystone.graph.truth import Fact, classify
from keystone.ontology import core_ontology

SYSTEM_PROMPT = """\
You answer questions about a company using ONLY the numbered facts provided.
Each fact has a status:
- current: believed true now
- history: was true in the past but has ended
- superseded: contradicted by a newer fact, no longer believed
- disputed: conflicts with another fact and neither clearly wins
Base the answer on current facts. If something changed over time, say what it was before,
what it is now, and when it changed, citing both. Mention disputed facts as uncertain.
Cite facts by number in square brackets, e.g. [2]. If the facts do not answer the question,
say that you do not know. Be concise."""


class _LlmAnswer(BaseModel):
    answer: str = Field(description="The answer, citing facts like [1].")
    cited_facts: list[int] = Field(description="Numbers of the facts the answer relies on.")


@dataclass(frozen=True)
class SourceInfo:
    ref: SourceRef
    observed_at: datetime | None


@dataclass(frozen=True)
class Answer:
    question: str
    text: str
    facts: list[Fact]  # numbered 1..n in this order
    cited: list[int]
    sources: dict[str, SourceInfo]  # episode uuid -> source


def _native(value: Any) -> datetime | None:
    if value is None:
        return None
    return value.to_native() if hasattr(value, "to_native") else value  # type: ignore[no-any-return]


async def _load_context(
    graphiti: Graphiti, edges: list[EntityEdge]
) -> tuple[dict[str, str], dict[str, SourceInfo]]:
    node_ids = list({u for e in edges for u in (e.source_node_uuid, e.target_node_uuid)})
    episode_ids = list({u for e in edges for u in e.episodes})
    nodes = await EntityNode.get_by_uuids(graphiti.driver, node_ids) if node_ids else []
    episodes = await EpisodicNode.get_by_uuids(graphiti.driver, episode_ids) if episode_ids else []
    names = {n.uuid: n.name for n in nodes}
    sources = {
        ep.uuid: SourceInfo(SourceRef.from_description(ep.source_description), _native(ep.valid_at))
        for ep in episodes
    }
    return names, sources


def _to_fact(edge: EntityEdge, names: dict[str, str], sources: dict[str, SourceInfo]) -> Fact:
    episode_dates = [
        s.observed_at for u in edge.episodes if (s := sources.get(u)) and s.observed_at
    ]
    observed = _native(edge.valid_at) or (max(episode_dates) if episode_dates else None)
    return Fact(
        id=edge.uuid,
        subject=names.get(edge.source_node_uuid, "?"),
        relation=edge.name.lower(),
        object=names.get(edge.target_node_uuid, "?"),
        text=edge.fact,
        observed_at=observed,
        ended_at=_native(edge.invalid_at) or _native(edge.expired_at),
        sources=tuple(u for u in edge.episodes if u in sources),
    )


def _describe(n: int, fact: Fact, sources: dict[str, SourceInfo]) -> str:
    when = fact.observed_at.date().isoformat() if fact.observed_at else "unknown date"
    files = ", ".join(sorted({sources[u].ref.file for u in fact.sources})) or "unknown source"
    return f"[{n}] ({fact.status}, since {when}, source: {files}) {fact.text}"


async def answer_question(graphiti: Graphiti, question: str, num_results: int = 15) -> Answer:
    group = get_settings().graph_group
    edges = await graphiti.search(question, group_ids=[group], num_results=num_results)
    names, sources = await _load_context(graphiti, edges)
    facts = classify([_to_fact(e, names, sources) for e in edges], core_ontology().rules)
    if not facts:
        return Answer(question, "I could not find anything about that in the graph.", [], [], {})

    fact_lines = "\n".join(_describe(i, f, sources) for i, f in enumerate(facts, start=1))
    response = await graphiti.llm_client.generate_response(
        [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=f"Facts:\n{fact_lines}\n\nQuestion: {question}"),
        ],
        response_model=_LlmAnswer,
    )
    parsed = _LlmAnswer.model_validate(response)
    cited = [n for n in parsed.cited_facts if 1 <= n <= len(facts)]
    return Answer(question, parsed.answer, facts, cited, sources)
