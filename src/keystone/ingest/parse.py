"""Document parsing with Docling (KAN-19).

Docling turns PDF, Word, Excel, PowerPoint, email, Markdown, text, HTML and CSV into a
structured document. We split it into sections (by heading, capped in size) and keep each
section's heading and page numbers for provenance. For emails, the sender and sent date are
read from the headers so the facts are dated by when the email was written.

Two entry points:
    parse_file(path)          a file on disk
    parse_document(document)  a connector Document (bytes or text), parsed in memory
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses, parsedate_to_datetime
from functools import lru_cache
from io import BytesIO
from pathlib import Path

from docling.datamodel.base_models import DocumentStream, InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker

from keystone.connectors.base import Document
from keystone.ingest.formats import FORMAT_NAMES, SUPPORTED_SUFFIXES

# File extension -> Docling format (MVP scope from CLAUDE.md, plus HTML and CSV).
FORMATS = {suffix: InputFormat[name] for suffix, name in FORMAT_NAMES.items()}
MAX_SECTION_CHARS = 3000
_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")


class ParseError(ValueError):
    """A file could not be read. The message is safe to show to users."""


@dataclass(frozen=True)
class Section:
    heading: str
    text: str
    pages: str  # e.g. "p. 2" or "p. 1-3"; "" when the format has no pages


@dataclass(frozen=True)
class ParsedDocument:
    file_name: str
    title: str
    doc_id: str  # content hash: the same content always gets the same id
    sections: list[Section]
    author: str | None = None  # e.g. the email sender
    written_at: datetime | None = None  # e.g. the email's sent date


def date_from_filename(name: str) -> datetime | None:
    """'2025-03-10_account_plan.docx' -> 2025-03-10 (UTC)."""
    match = _DATE_PREFIX.match(name)
    if not match:
        return None
    return datetime.strptime(match.group(1), "%Y-%m-%d").replace(tzinfo=UTC)


def _title_from_filename(name: str) -> str:
    stem = _DATE_PREFIX.sub("", Path(name).stem).strip("_- ")
    return stem.replace("_", " ").strip().capitalize() or name


@lru_cache
def _converter() -> DocumentConverter:
    pdf_options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)  # OCR: not in MVP
    return DocumentConverter(
        allowed_formats=list(set(FORMATS.values())),
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)},
    )


def _page_label(pages: set[int]) -> str:
    if not pages:
        return ""
    low, high = min(pages), max(pages)
    return f"p. {low}" if low == high else f"p. {low}-{high}"


def _docling_sections(
    file_name: str, data: bytes, default_heading: str
) -> tuple[str, list[Section]]:
    try:
        result = _converter().convert(DocumentStream(name=file_name, stream=BytesIO(data)))
    except Exception as exc:
        raise ParseError(f"Could not read {file_name}: {type(exc).__name__}: {exc}") from exc

    groups: list[tuple[str, list[str], set[int]]] = []
    title = ""
    for chunk in HierarchicalChunker().chunk(result.document):
        headings = list(getattr(chunk.meta, "headings", None) or [])
        if headings and not title:
            title = headings[0]
        heading = headings[-1] if headings else default_heading
        pages = {
            prov.page_no
            for item in getattr(chunk.meta, "doc_items", [])
            for prov in getattr(item, "prov", [])
        }
        text = chunk.text.strip()
        if not text:
            continue
        last = groups[-1] if groups else None
        if last and last[0] == heading and len("\n".join(last[1])) + len(text) < MAX_SECTION_CHARS:
            last[1].append(text)
            last[2].update(pages)
        else:
            groups.append((heading, [text], set(pages)))
    return title, [Section(h, "\n".join(texts), _page_label(p)) for h, texts, p in groups]


def _email_headers(data: bytes) -> tuple[str | None, datetime | None, str | None]:
    """(sender, sent date, subject) from a .eml file's headers."""
    message = BytesParser(policy=policy.default).parsebytes(data, headersonly=True)
    senders = getaddresses([str(message.get("From", ""))])
    sender = next((name or address for name, address in senders if name or address), None)
    sent: datetime | None = None
    if message.get("Date"):
        try:
            sent = parsedate_to_datetime(str(message["Date"]))
        except (TypeError, ValueError):
            sent = None
    if sent is not None and sent.tzinfo is None:
        sent = sent.replace(tzinfo=UTC)
    subject = str(message.get("Subject", "")) or None
    return sender, sent, subject


def parse_bytes(file_name: str, data: bytes) -> ParsedDocument:
    """Parse file content held in memory. The extension of `file_name` selects the reader."""
    suffix = Path(file_name).suffix.lower()
    if suffix not in FORMATS:
        raise ParseError(f"Unsupported file type '{suffix}'. Supported: {SUPPORTED_SUFFIXES}")
    if not data.strip():
        raise ParseError(f"{file_name} is empty")

    fallback_title = _title_from_filename(file_name)
    title, sections = _docling_sections(file_name, data, fallback_title)
    if not sections:
        raise ParseError(f"No text found in {file_name}")

    author = written_at = None
    if suffix == ".eml":
        author, written_at, subject = _email_headers(data)
        title = subject or title
    return ParsedDocument(
        file_name=file_name,
        title=title or fallback_title,
        doc_id=hashlib.sha256(data).hexdigest()[:16],
        sections=sections,
        author=author,
        written_at=written_at,
    )


def parse_file(path: Path) -> ParsedDocument:
    return parse_bytes(path.name, path.read_bytes())


def parse_document(document: Document) -> ParsedDocument:
    """Parse a connector Document. Text content is read as Markdown (headings become sections)."""
    if document.content is not None:
        assert document.file_name is not None  # guaranteed by Document validation
        parsed = parse_bytes(document.file_name, document.content)
    else:
        name = document.file_name or f"{document.id}.md"
        if Path(name).suffix.lower() not in FORMATS:
            name = f"{name}.md"
        parsed = parse_bytes(name, (document.text or "").encode("utf-8"))
    return ParsedDocument(
        file_name=parsed.file_name,
        title=document.title or parsed.title,
        doc_id=document.content_hash,
        sections=parsed.sections,
        author=parsed.author or document.author,
        written_at=parsed.written_at or document.modified_at,
    )
