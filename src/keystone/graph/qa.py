"""Answer a question from the knowledge graph, with sources.

1. Graphiti hybrid search finds the most relevant facts (edges).
2. Each fact is linked back to the document sections it came from (provenance).
3. The truth engine labels facts as current / history / superseded / disputed.
4. The LLM writes an answer using ONLY those numbered facts and cites them.
"""

from dataclasses import dataclass

from graphiti_core.prompts.models import Message
from pydantic import BaseModel, Field

from keystone.graph.engine import KeystoneGraph
from keystone.graph.facts import load_facts
from keystone.graph.provenance import Provenance
from keystone.graph.truth import Fact

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
class Answer:
    question: str
    text: str
    facts: list[Fact]  # numbered 1..n in this order
    cited: list[int]
    sources: dict[str, Provenance]  # episode uuid -> provenance


def describe_fact(n: int, fact: Fact, sources: dict[str, Provenance]) -> str:
    when = fact.observed_at.date().isoformat() if fact.observed_at else "unknown date"
    where = "; ".join(sorted({sources[u].location for u in fact.sources})) or "unknown source"
    return f"[{n}] ({fact.status}, since {when}, source: {where}) {fact.text}"


async def answer_question(graph: KeystoneGraph, question: str, num_results: int = 15) -> Answer:
    edges = await graph.graphiti.search(question, group_ids=[graph.group], num_results=num_results)
    facts, sources = await load_facts(graph, edges)
    if not facts:
        return Answer(question, "I could not find anything about that in the graph.", [], [], {})

    fact_lines = "\n".join(describe_fact(i, f, sources) for i, f in enumerate(facts, start=1))
    response = await graph.graphiti.llm_client.generate_response(
        [
            Message(role="system", content=SYSTEM_PROMPT),
            Message(role="user", content=f"Facts:\n{fact_lines}\n\nQuestion: {question}"),
        ],
        response_model=_LlmAnswer,
    )
    parsed = _LlmAnswer.model_validate(response)
    cited = [n for n in parsed.cited_facts if 1 <= n <= len(facts)]
    return Answer(question, parsed.answer, facts, cited, sources)
