"""Document parsing with Docling (KAN-19, minimal).

Docling turns PDF, Word, Excel, PowerPoint, Markdown and HTML into a structured document.
We split it into sections (by heading, capped in size) and keep each section's heading and
page numbers for provenance. Plain .txt files are read directly.
"""

import hashlib
import re
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import lru_cache
from pathlib import Path

from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import DocumentConverter, PdfFormatOption
from docling_core.transforms.chunker.hierarchical_chunker import HierarchicalChunker

DOCLING_SUFFIXES = {
    ".pdf": InputFormat.PDF,
    ".docx": InputFormat.DOCX,
    ".xlsx": InputFormat.XLSX,
    ".pptx": InputFormat.PPTX,
    ".md": InputFormat.MD,
    ".html": InputFormat.HTML,
    ".htm": InputFormat.HTML,
}
SUPPORTED_SUFFIXES = sorted([*DOCLING_SUFFIXES, ".txt"])
MAX_SECTION_CHARS = 3000
_DATE_PREFIX = re.compile(r"^(\d{4}-\d{2}-\d{2})")


@dataclass(frozen=True)
class Section:
    heading: str
    text: str
    pages: str  # e.g. "p. 2" or "p. 1-3"; "" when the format has no pages


@dataclass(frozen=True)
class ParsedDocument:
    file_name: str
    title: str
    doc_id: str  # content hash: the same file uploaded twice gets the same id
    sections: list[Section]


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
    pdf_options = PdfPipelineOptions(do_ocr=False, do_table_structure=True)
    return DocumentConverter(
        allowed_formats=list(set(DOCLING_SUFFIXES.values())),
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=pdf_options)},
    )


def _page_label(pages: set[int]) -> str:
    if not pages:
        return ""
    low, high = min(pages), max(pages)
    return f"p. {low}" if low == high else f"p. {low}-{high}"


def _docling_sections(path: Path, default_heading: str) -> tuple[str, list[Section]]:
    document = _converter().convert(path).document
    groups: list[tuple[str, list[str], set[int]]] = []
    title = ""
    for chunk in HierarchicalChunker().chunk(document):
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
    sections = [Section(h, "\n".join(texts), _page_label(p)) for h, texts, p in groups]
    return title, sections


def parse_file(path: Path) -> ParsedDocument:
    suffix = path.suffix.lower()
    if suffix not in SUPPORTED_SUFFIXES:
        raise ValueError(f"Unsupported file type '{suffix}'. Supported: {SUPPORTED_SUFFIXES}")
    data = path.read_bytes()
    doc_id = hashlib.sha256(data).hexdigest()[:16]
    fallback_title = _title_from_filename(path.name)

    if suffix == ".txt":
        title, sections = "", [Section(fallback_title, data.decode("utf-8", "replace"), "")]
    else:
        title, sections = _docling_sections(path, fallback_title)
    if not sections:
        raise ValueError(f"No text found in {path.name}")
    return ParsedDocument(path.name, title or fallback_title, doc_id, sections)
