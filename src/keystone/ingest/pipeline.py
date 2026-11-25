"""Ingestion pipeline (KAN-23): connector Document -> parse -> graph, with provenance.

    parse (Docling) -> one episode per section -> Graphiti extraction with the ontology
    (entity resolution and fact dating happen inside Graphiti) -> Keystone provenance record

Use `ingest_document` for one document and `sync_connector` for a whole source.
"""

import hashlib
from collections.abc import Callable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from urllib.parse import urlparse
from urllib.request import url2pathname

from keystone.connectors.base import BaseConnector, Document
from keystone.graph.engine import KeystoneGraph, StoredDocument
from keystone.graph.provenance import Provenance
from keystone.ingest.parse import ParseError, date_from_filename, parse_document

# Structured database rows need less interpretation than free text.
CONFIDENCE_BY_KIND = {"database": 0.95, "document": 0.8, "agent": 0.7}

Progress = Callable[[str], None]


@dataclass
class IngestResult:
    document_id: str
    title: str
    status: str  # ingested | skipped | failed
    sections: int = 0
    message: str = ""


@dataclass
class SyncReport:
    results: list[IngestResult] = field(default_factory=list)

    def count(self, status: str) -> int:
        return sum(1 for r in self.results if r.status == status)

    def summary(self) -> str:
        return (
            f"{self.count('ingested')} ingested, {self.count('skipped')} unchanged, "
            f"{self.count('failed')} failed"
        )


def observed_date(document: Document, written_at: datetime | None) -> datetime:
    """When the content was written: the document's own date, else the best available guess.

    A file's modification time is the weakest clue: copying or re-saving a file changes it.
    """
    for candidate in (
        written_at,  # e.g. an email's sent date
        date_from_filename(document.file_name or ""),  # e.g. 2025-03-10_account_plan.docx
        date_from_filename(document.title),
        document.modified_at,
    ):
        if candidate is not None:
            return candidate if candidate.tzinfo else candidate.replace(tzinfo=UTC)
    return datetime.now(UTC)


def source_kind(document: Document) -> str:
    if document.metadata.get("table"):
        return "database"
    if document.source.startswith("agent:"):
        return "agent"
    return "document"


async def ingest_document(
    graph: KeystoneGraph,
    document: Document,
    business_function: str | None = None,
    progress: Progress | None = None,
) -> IngestResult:
    """Parse one document and store each section with provenance. Unchanged content is skipped."""
    try:
        parsed = parse_document(document)
    except ParseError as exc:
        return IngestResult(document.id, document.title, "failed", message=str(exc))
    if await graph.has_document(parsed.doc_id):
        return IngestResult(document.id, parsed.title, "skipped", message="already in the graph")

    function = (
        business_function or document.business_function or graph.settings.default_business_function
    )
    kind = source_kind(document)
    observed = observed_date(document, parsed.written_at)
    total = len(parsed.sections)
    for i, section in enumerate(parsed.sections, start=1):
        if progress:
            progress(f"{parsed.title}: section {i}/{total} ({section.heading})")
        provenance = Provenance(
            doc_id=parsed.doc_id,
            document_id=document.id,
            source=document.source,
            title=parsed.title,
            file=document.file_name,
            section=section.heading,
            pages=section.pages,
            author=parsed.author or document.author,
            observed_at=observed,
            business_function=function,
            confidence=CONFIDENCE_BY_KIND[kind],
            url=document.url,
            kind=kind,
        )
        body = f"Document: {parsed.title}\nSection: {section.heading}\n\n{section.text}"
        await graph.add_section(provenance, body, progress)
    return IngestResult(document.id, parsed.title, "ingested", sections=total)


def save_upload(
    upload_dir: Path, file_name: str, content: bytes, written_at: datetime | None = None
) -> Document:
    """Keep a copy of an uploaded file (so it can be re-ingested) and return it as a Document."""
    upload_dir.mkdir(parents=True, exist_ok=True)
    safe_name = "".join(c if c.isalnum() or c in "._-" else "_" for c in Path(file_name).name)
    digest = hashlib.sha256(content).hexdigest()[:12]
    path = (upload_dir / f"{digest}_{safe_name}").resolve()
    path.write_bytes(content)
    return Document(
        id=file_name,
        source="upload:ui",
        title=Path(file_name).stem.replace("_", " "),
        file_name=Path(file_name).name,
        content=content,
        modified_at=written_at,
        url=path.as_uri(),
    )


def _local_path(url: str | None) -> Path | None:
    if not url or not url.startswith("file:"):
        return None
    path = Path(url2pathname(urlparse(url).path))
    return path if path.is_file() else None


async def reingest(
    graph: KeystoneGraph, stored: StoredDocument, progress: Progress | None = None
) -> IngestResult:
    """Delete a document's facts and ingest its original file again (e.g. after a fix)."""
    path = _local_path(stored.url)
    if path is None:
        return IngestResult(
            stored.doc_id, stored.title, "failed", message="the original file is not available"
        )
    document = Document(
        id=stored.file or path.name,
        source=stored.source,
        title=stored.title,
        file_name=stored.file or path.name,
        content=path.read_bytes(),
        modified_at=stored.observed_at,
        author=stored.author,
        url=stored.url,
    )
    await graph.delete_document(stored.doc_id)
    return await ingest_document(graph, document, stored.business_function, progress)


async def sync_connector(
    graph: KeystoneGraph,
    connector: BaseConnector,  # type: ignore[type-arg]
    stream: str | None = None,
    since: datetime | None = None,
    business_function: str | None = None,
    progress: Progress | None = None,
) -> SyncReport:
    """Read a connector and ingest every document. One failure does not stop the sync."""
    report = SyncReport()
    for document in connector.read(stream=stream, since=since):
        if progress:
            progress(f"Reading {document.title}")
        try:
            result = await ingest_document(graph, document, business_function, progress)
        except Exception as exc:  # keep going; report the failure
            result = IngestResult(
                document.id, document.title, "failed", message=f"{type(exc).__name__}: {exc}"
            )
        report.results.append(result)
    return report
