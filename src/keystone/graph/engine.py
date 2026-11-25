"""Graphiti integration (KAN-23, minimal): connect, store document sections, find sources.

Each document section becomes one Graphiti "episode". Graphiti's LLM extracts entities and
relations constrained to the Keystone ontology (via to_graphiti), resolves duplicates and keeps
validity dates on every fact. The episode's `source_description` records where the text came
from, so every fact can be traced back to a file, section and page.
"""

from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from graphiti_core import Graphiti
from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.openai_client import OpenAIClient
from graphiti_core.nodes import EpisodeType
from graphiti_core.utils.maintenance.graph_data_operations import clear_data

from keystone.config import Settings, get_settings
from keystone.ingest.parse import ParsedDocument
from keystone.ontology import active_ontology, to_graphiti


class MissingApiKeyError(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceRef:
    """Where a piece of text came from. Stored as the episode's `source_description`."""

    file: str
    section: str
    pages: str
    business_function: str
    doc_id: str

    def to_description(self) -> str:
        return " | ".join(
            [self.file, self.section, self.pages, self.business_function, f"doc:{self.doc_id}"]
        )

    @classmethod
    def from_description(cls, text: str) -> "SourceRef":
        parts = [p.strip() for p in text.split(" | ")]
        if len(parts) != 5 or not parts[4].startswith("doc:"):
            return cls(file=text, section="", pages="", business_function="", doc_id="")
        return cls(parts[0], parts[1], parts[2], parts[3], parts[4].removeprefix("doc:"))


def make_graphiti(settings: Settings | None = None) -> Graphiti:
    settings = settings or get_settings()
    api_key = settings.openai_api_key.get_secret_value()
    if not api_key:
        raise MissingApiKeyError("OPENAI_API_KEY is not set. Add it to the .env file.")
    llm_config = LLMConfig(
        api_key=api_key, model=settings.llm_model, small_model=settings.llm_model
    )
    embedder_config = OpenAIEmbedderConfig(api_key=api_key)
    if settings.embedding_model:
        embedder_config.embedding_model = settings.embedding_model
    return Graphiti(
        settings.neo4j_uri,
        settings.neo4j_user,
        settings.neo4j_password.get_secret_value(),
        llm_client=OpenAIClient(config=llm_config),
        embedder=OpenAIEmbedder(config=embedder_config),
        cross_encoder=OpenAIRerankerClient(config=LLMConfig(api_key=api_key)),
    )


@asynccontextmanager
async def open_graph(settings: Settings | None = None) -> AsyncIterator[Graphiti]:
    graphiti = make_graphiti(settings)
    try:
        yield graphiti
    finally:
        await graphiti.close()  # type: ignore[no-untyped-call]  # untyped in graphiti-core


async def is_ingested(graphiti: Graphiti, doc_id: str, group: str) -> bool:
    records, _, _ = await graphiti.driver.execute_query(
        "MATCH (e:Episodic {group_id: $group}) WHERE e.source_description ENDS WITH $suffix "
        "RETURN count(e) AS n",
        group=group,
        suffix=f"doc:{doc_id}",
    )
    return bool(records and records[0]["n"] > 0)


async def ingest_document(
    graphiti: Graphiti,
    doc: ParsedDocument,
    business_function: str,
    observed_at: datetime,
    on_progress: Callable[[int, int, str], None] | None = None,
    settings: Settings | None = None,
) -> int:
    """Store every section of a parsed document as an episode. Returns the number stored.

    Skips the document if a file with the same content was already ingested.
    """
    settings = settings or get_settings()
    ontology = active_ontology()
    if business_function not in ontology.business_functions:
        raise ValueError(f"business_function must be one of {ontology.business_functions}")
    if await is_ingested(graphiti, doc.doc_id, settings.graph_group):
        return 0
    if observed_at.tzinfo is None:
        observed_at = observed_at.replace(tzinfo=UTC)

    schema = to_graphiti(ontology)
    total = len(doc.sections)
    for i, section in enumerate(doc.sections, start=1):
        if on_progress:
            on_progress(i, total, section.heading)
        source = SourceRef(
            file=doc.file_name,
            section=section.heading,
            pages=section.pages,
            business_function=business_function,
            doc_id=doc.doc_id,
        )
        await graphiti.add_episode(
            name=f"{doc.file_name} - {section.heading}"[:200],
            episode_body=f"Document: {doc.title}\nSection: {section.heading}\n\n{section.text}",
            source_description=source.to_description(),
            reference_time=observed_at,
            source=EpisodeType.text,
            group_id=settings.graph_group,
            **schema.add_episode_kwargs(),
        )
    return total


@dataclass(frozen=True)
class IngestedDocument:
    file: str
    business_function: str
    doc_id: str
    sections: int
    observed_at: datetime


async def list_documents(graphiti: Graphiti, group: str) -> list[IngestedDocument]:
    records, _, _ = await graphiti.driver.execute_query(
        "MATCH (e:Episodic {group_id: $group}) "
        "RETURN e.source_description AS source, e.valid_at AS observed_at",
        group=group,
    )
    docs: dict[str, IngestedDocument] = {}
    for record in records:
        ref = SourceRef.from_description(record["source"])
        observed = record["observed_at"]
        observed = observed.to_native() if hasattr(observed, "to_native") else observed
        key = ref.doc_id or ref.file
        count = docs[key].sections + 1 if key in docs else 1
        docs[key] = IngestedDocument(ref.file, ref.business_function, ref.doc_id, count, observed)
    return sorted(docs.values(), key=lambda d: d.observed_at)


async def delete_all(graphiti: Graphiti, group: str) -> None:
    await clear_data(graphiti.driver, group_ids=[group])
