"""KAN-23: ingestion pipeline (Document -> parse -> graph with provenance), no LLM needed."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from keystone.config import Settings
from keystone.connectors import Document
from keystone.connectors.folder import FolderConnector
from keystone.graph.engine import MissingApiKeyError, make_graphiti
from keystone.graph.provenance import Provenance
from keystone.ingest.pipeline import (
    ingest_document,
    observed_date,
    source_kind,
    sync_connector,
)
from keystone.ontology import active_ontology

DATA = Path(__file__).resolve().parents[1] / "demo" / "data"


class FakeGraph:
    """Records what the pipeline would store, instead of calling Graphiti."""

    def __init__(self) -> None:
        self.settings = Settings(default_business_function="Operations")
        self.ontology = active_ontology()
        self.sections: list[tuple[Provenance, str]] = []

    async def has_document(self, doc_id: str) -> bool:
        return any(p.doc_id == doc_id for p, _ in self.sections)

    async def add_section(self, provenance: Provenance, body: str) -> Any:
        self.sections.append((provenance, body))


def text_doc(**overrides: Any) -> Document:
    fields: dict[str, Any] = {
        "id": "notes/1",
        "source": "notes:test",
        "title": "Handover",
        "text": "# Handover\n\nBilal Ahmed manages Harbor Hotels Group.",
    }
    return Document(**{**fields, **overrides})


async def test_sections_are_stored_with_full_provenance() -> None:
    graph = FakeGraph()
    memo = DATA / "2026-09-15_account_reassignment_memo.docx"
    document = Document(
        id=memo.name,
        source="folder:samples",
        title="Reassignment memo",
        file_name=memo.name,
        content=memo.read_bytes(),
        author="Omar Siddiqui",
        url="file:///memo.docx",
    )
    result = await ingest_document(graph, document, "Sales")  # type: ignore[arg-type]
    assert (result.status, result.sections) == ("ingested", 3)
    provenance, body = graph.sections[1]
    assert provenance.source == "folder:samples"
    assert provenance.file == memo.name
    assert provenance.section == "Harbor Hotels Group"
    assert provenance.author == "Omar Siddiqui"
    assert provenance.business_function == "Sales"
    assert provenance.observed_at == datetime(2026, 9, 15, tzinfo=UTC)  # from the file name
    assert provenance.kind == "document" and provenance.confidence == 0.8
    assert body.startswith("Document: Memo: Account manager changes\nSection: Harbor Hotels Group")
    assert "Bilal Ahmed is the account manager" in body


async def test_unchanged_documents_are_skipped() -> None:
    graph = FakeGraph()
    first = await ingest_document(graph, text_doc())  # type: ignore[arg-type]
    again = await ingest_document(graph, text_doc(id="copy"))  # type: ignore[arg-type]
    assert (first.status, again.status) == ("ingested", "skipped")
    assert len(graph.sections) == 1


async def test_business_function_priority() -> None:
    graph = FakeGraph()
    await ingest_document(graph, text_doc(business_function="Finance"))  # type: ignore[arg-type]
    await ingest_document(graph, text_doc(text="other", business_function="Finance"), "HR")  # type: ignore[arg-type]
    await ingest_document(graph, text_doc(text="third"))  # type: ignore[arg-type]
    functions = [p.business_function for p, _ in graph.sections]
    assert functions == ["Finance", "HR", "Operations"]  # source, explicit, default


async def test_unreadable_document_fails_without_raising() -> None:
    graph = FakeGraph()
    broken = Document(
        id="b", source="s:x", title="b", file_name="broken.docx", content=b"not a docx"
    )
    result = await ingest_document(graph, broken)  # type: ignore[arg-type]
    assert result.status == "failed" and "Could not read" in result.message


async def test_sync_connector_reports_each_document(tmp_path: Path) -> None:
    (tmp_path / "a.md").write_text("# A\n\nAli works for Acme.", encoding="utf-8")
    (tmp_path / "b.docx").write_bytes(b"broken")
    graph = FakeGraph()
    connector = FolderConnector({"path": tmp_path}, "t")
    report = await sync_connector(graph, connector)  # type: ignore[arg-type]
    assert [r.status for r in report.results] == ["ingested", "failed"]
    assert report.summary() == "1 ingested, 0 unchanged, 1 failed"
    again = await sync_connector(graph, connector)  # type: ignore[arg-type]
    assert again.count("skipped") == 1


def test_observed_date_priority() -> None:
    written = datetime(2026, 9, 15, 8, 30, tzinfo=UTC)
    modified = datetime(2027, 1, 1, tzinfo=UTC)
    doc = text_doc(modified_at=modified, file_name="2025-03-10_plan.md")
    assert observed_date(doc, written) == written  # the document's own date wins
    assert observed_date(doc, None) == modified
    assert observed_date(text_doc(file_name="2025-03-10_plan.md"), None) == datetime(
        2025, 3, 10, tzinfo=UTC
    )


def test_source_kind_and_confidence() -> None:
    assert source_kind(text_doc(metadata={"table": "customers"})) == "database"
    assert source_kind(text_doc(source="agent:sales-bot")) == "agent"
    assert source_kind(text_doc()) == "document"


def test_missing_api_key_is_a_clear_error() -> None:
    with pytest.raises(MissingApiKeyError, match="GEMINI_API_KEY is not set"):
        make_graphiti(Settings(llm_provider="gemini", gemini_api_key=""))  # type: ignore[arg-type]
    with pytest.raises(MissingApiKeyError, match="OPENAI_API_KEY is not set"):
        make_graphiti(Settings(llm_provider="openai", openai_api_key=""))  # type: ignore[arg-type]


def test_gemini_is_the_default_provider() -> None:
    graphiti = make_graphiti(Settings(gemini_api_key="test-key"))  # type: ignore[arg-type]
    assert type(graphiti.llm_client).__name__ == "GeminiClient"
    assert type(graphiti.embedder).__name__ == "GeminiEmbedder"
    assert type(graphiti.cross_encoder).__name__ == "GeminiRerankerClient"


def test_provenance_location() -> None:
    p = Provenance(
        doc_id="d", document_id="x", source="s:x", title="Plan", file="plan.pdf",
        section="Account team", pages="p. 2", observed_at=datetime.now(UTC),
        business_function="Sales",
    )  # fmt: skip
    assert p.location == "plan.pdf › Account team (p. 2)"
    assert Provenance.from_properties(p.to_properties()) == p
