"""Graphiti integration (KAN-23): the knowledge graph behind Keystone.

Each document section is stored as one Graphiti "episode". Graphiti's LLM extracts entities
and relations constrained to the Keystone ontology (via to_graphiti), merges duplicates and
keeps validity dates on every fact. Keystone adds a provenance record to every episode (see
provenance.py), so each fact traces back to a source, author, date and business function.
"""

import asyncio
from collections.abc import AsyncIterator, Callable
from contextlib import asynccontextmanager
from dataclasses import dataclass
from datetime import UTC, datetime

from graphiti_core import Graphiti
from graphiti_core.graphiti import AddEpisodeResults
from graphiti_core.llm_client.config import LLMConfig
from graphiti_core.llm_client.errors import RateLimitError
from graphiti_core.nodes import EpisodeType
from graphiti_core.utils.maintenance.graph_data_operations import clear_data

from keystone.config import Settings, get_settings
from keystone.graph.provenance import PROVENANCE_REL, SOURCE_LABEL, Provenance
from keystone.ontology import Ontology, active_ontology, to_graphiti
from keystone.ontology.to_graphiti import GraphitiSchema


class MissingApiKeyError(RuntimeError):
    pass


# Tested with a new Gemini API key (Nov 2026). Graphiti's built-in Gemini defaults
# (gemini-3-flash-preview, gemini-2.5-flash-lite, text-embedding-001) are not available to new
# keys, so Keystone sets its own. Override with LLM_MODEL, LLM_SMALL_MODEL, EMBEDDING_MODEL.
GEMINI_DEFAULTS = {
    "model": "gemini-3.5-flash",
    "small_model": "gemini-3.5-flash-lite",
    "embedding_model": "gemini-embedding-001",
}


_TRANSIENT_MARKERS = {
    "503": "Provider busy",
    "unavailable": "Provider busy",
    "high demand": "Provider busy",
    "overloaded": "Provider busy",
    "timed out": "Provider timeout",
    "timeout": "Provider timeout",
}


def transient_problem(exc: BaseException) -> str | None:
    """A short label if the error is temporary (rate limit, overload, timeout), else None.

    Graphiti wraps provider errors, so the whole cause chain is checked.
    """
    seen: BaseException | None = exc
    while seen is not None:
        if isinstance(seen, RateLimitError):
            return "Rate limit reached"
        text = str(seen).lower()
        for marker, label in _TRANSIENT_MARKERS.items():
            if marker in text:
                return label
        seen = seen.__cause__ or seen.__context__
    return None


def make_graphiti(settings: Settings | None = None) -> Graphiti:
    """A Graphiti client for the configured LLM provider (Gemini or OpenAI)."""
    settings = settings or get_settings()
    api_key = settings.api_key
    if not api_key:
        raise MissingApiKeyError(f"{settings.api_key_name} is not set. Add it to the .env file.")
    uri, user = settings.neo4j_uri, settings.neo4j_user
    password = settings.neo4j_password.get_secret_value()

    if settings.llm_provider == "gemini":
        from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient
        from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
        from graphiti_core.llm_client.gemini_client import GeminiClient

        model = settings.llm_model or GEMINI_DEFAULTS["model"]
        small_model = settings.llm_small_model or GEMINI_DEFAULTS["small_model"]
        embedding_model = settings.embedding_model or GEMINI_DEFAULTS["embedding_model"]
        return Graphiti(
            uri,
            user,
            password,
            max_coroutines=settings.llm_max_concurrency,
            llm_client=GeminiClient(
                config=LLMConfig(api_key=api_key, model=model, small_model=small_model)
            ),
            embedder=GeminiEmbedder(
                config=GeminiEmbedderConfig(api_key=api_key, embedding_model=embedding_model)
            ),
            cross_encoder=GeminiRerankerClient(
                config=LLMConfig(api_key=api_key, model=small_model)
            ),
        )

    llm_config = LLMConfig(
        api_key=api_key, model=settings.llm_model, small_model=settings.llm_small_model
    )

    from graphiti_core.cross_encoder.openai_reranker_client import OpenAIRerankerClient
    from graphiti_core.embedder.openai import OpenAIEmbedder, OpenAIEmbedderConfig
    from graphiti_core.llm_client.openai_client import OpenAIClient

    openai_embedder = OpenAIEmbedderConfig(api_key=api_key)
    if settings.embedding_model:
        openai_embedder.embedding_model = settings.embedding_model
    return Graphiti(
        uri,
        user,
        password,
        max_coroutines=settings.llm_max_concurrency,
        llm_client=OpenAIClient(config=llm_config),
        embedder=OpenAIEmbedder(config=openai_embedder),
        cross_encoder=OpenAIRerankerClient(config=LLMConfig(api_key=api_key)),
    )


@dataclass(frozen=True)
class StoredDocument:
    doc_id: str
    title: str
    file: str | None
    source: str
    business_function: str
    observed_at: datetime
    sections: int


class KeystoneGraph:
    """Graphiti plus Keystone provenance. Use `open_graph()` to get one."""

    def __init__(
        self, graphiti: Graphiti, settings: Settings | None = None, ontology: Ontology | None = None
    ):
        self.graphiti = graphiti
        self.settings = settings or get_settings()
        self.ontology = ontology or active_ontology()
        self.group = self.settings.graph_group
        self._schema: GraphitiSchema | None = None

    @property
    def schema(self) -> GraphitiSchema:
        if self._schema is None:
            self._schema = to_graphiti(self.ontology)
        return self._schema

    async def query(self, cypher: str, **params: object) -> list[dict[str, object]]:
        records, _, _ = await self.graphiti.driver.execute_query(cypher, **params)
        return [dict(r) for r in records]

    async def setup(self) -> None:
        """Create Graphiti's indexes plus Keystone's own (safe to run on every start)."""
        await self.graphiti.build_indices_and_constraints()
        await self.query(
            f"CREATE INDEX keystone_source_episode IF NOT EXISTS "
            f"FOR (s:{SOURCE_LABEL}) ON (s.episode_uuid)"
        )
        await self.query(
            f"CREATE INDEX keystone_source_doc IF NOT EXISTS FOR (s:{SOURCE_LABEL}) ON (s.doc_id)"
        )

    async def add_section(
        self, provenance: Provenance, body: str, progress: Callable[[str], None] | None = None
    ) -> AddEpisodeResults:
        """Store one section: Graphiti extracts facts, Keystone records where they came from.

        On a provider rate limit the section is retried after a growing wait. Graphiti saves an
        episode only after all its LLM calls succeed, so a retry never stores anything twice.
        """
        if provenance.business_function not in self.ontology.business_functions:
            raise ValueError(
                f"business_function must be one of {self.ontology.business_functions}, "
                f"got '{provenance.business_function}'"
            )
        observed = provenance.observed_at
        if observed.tzinfo is None:
            observed = observed.replace(tzinfo=UTC)

        attempt = 0
        while True:
            try:
                result = await self.graphiti.add_episode(
                    name=f"{provenance.title} - {provenance.section}"[:200],
                    episode_body=body,
                    source_description=f"{provenance.location} ({provenance.business_function})",
                    reference_time=observed,
                    source=EpisodeType.text,
                    group_id=self.group,
                    **self.schema.add_episode_kwargs(),
                )
                break
            except Exception as exc:
                problem = transient_problem(exc)
                attempt += 1
                if problem is None or attempt > self.settings.rate_limit_retries:
                    raise
                wait = self.settings.rate_limit_wait_seconds * 2 ** (attempt - 1)
                if progress:
                    progress(f"{problem}; waiting {wait:.0f}s (retry {attempt})")
                await asyncio.sleep(wait)
        await self.record_provenance(result.episode.uuid, provenance)
        return result

    async def record_provenance(self, episode_uuid: str, provenance: Provenance) -> None:
        """Attach Keystone's provenance record to a Graphiti episode."""
        await self.query(
            f"MATCH (e:Episodic {{uuid: $episode_uuid}}) "
            f"CREATE (e)-[:{PROVENANCE_REL}]->(s:{SOURCE_LABEL}) "
            f"SET s = $props, s.episode_uuid = $episode_uuid, s.group_id = $group",
            episode_uuid=episode_uuid,
            props=provenance.to_properties(),
            group=self.group,
        )

    async def has_document(self, doc_id: str) -> bool:
        rows = await self.query(
            f"MATCH (s:{SOURCE_LABEL} {{doc_id: $doc_id, group_id: $group}}) RETURN count(s) AS n",
            doc_id=doc_id,
            group=self.group,
        )
        return bool(rows and int(str(rows[0]["n"])) > 0)

    async def provenance(self, episode_uuids: list[str]) -> dict[str, Provenance]:
        """Provenance for each episode uuid (episodes without a record are left out)."""
        if not episode_uuids:
            return {}
        rows = await self.query(
            f"MATCH (s:{SOURCE_LABEL}) WHERE s.episode_uuid IN $uuids "
            f"RETURN s.episode_uuid AS uuid, properties(s) AS props",
            uuids=episode_uuids,
        )
        return {
            str(r["uuid"]): Provenance.from_properties(dict(r["props"]))  # type: ignore[call-overload]
            for r in rows
        }

    async def list_documents(self) -> list[StoredDocument]:
        rows = await self.query(
            f"MATCH (s:{SOURCE_LABEL} {{group_id: $group}}) "
            f"RETURN s.doc_id AS doc_id, collect(properties(s)) AS sections",
            group=self.group,
        )
        documents = []
        for row in rows:
            sections = [Provenance.from_properties(dict(p)) for p in row["sections"]]  # type: ignore[attr-defined]
            first = sections[0]
            documents.append(
                StoredDocument(
                    doc_id=first.doc_id,
                    title=first.title,
                    file=first.file,
                    source=first.source,
                    business_function=first.business_function,
                    observed_at=first.observed_at,
                    sections=len(sections),
                )
            )
        return sorted(documents, key=lambda d: d.observed_at)

    async def delete_document(self, doc_id: str) -> int:
        """Remove a document's episodes (and facts only it supported). Returns episodes removed."""
        rows = await self.query(
            f"MATCH (s:{SOURCE_LABEL} {{doc_id: $doc_id, group_id: $group}}) "
            f"RETURN s.episode_uuid AS uuid",
            doc_id=doc_id,
            group=self.group,
        )
        for row in rows:
            await self.graphiti.remove_episode(str(row["uuid"]))
        await self.query(
            f"MATCH (s:{SOURCE_LABEL} {{doc_id: $doc_id, group_id: $group}}) DETACH DELETE s",
            doc_id=doc_id,
            group=self.group,
        )
        return len(rows)

    async def delete_all(self) -> None:
        """Delete everything in this Keystone graph (Graphiti data and provenance)."""
        await clear_data(self.graphiti.driver, group_ids=[self.group])
        # Keystone's own nodes: provenance and reviewers' decisions.
        await self.query(
            "MATCH (s) WHERE s.group_id = $group AND any(label IN labels(s) "
            "WHERE label STARTS WITH 'Keystone') DETACH DELETE s",
            group=self.group,
        )


@asynccontextmanager
async def open_graph(settings: Settings | None = None) -> AsyncIterator[KeystoneGraph]:
    settings = settings or get_settings()
    graphiti = make_graphiti(settings)
    try:
        yield KeystoneGraph(graphiti, settings)
    finally:
        await graphiti.close()  # type: ignore[no-untyped-call]  # untyped in graphiti-core
