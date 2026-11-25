"""KAN-19 (minimal): Docling parsing of the sample documents. No LLM or network needed,
except the PDF test, which needs Docling's layout models (downloaded on first use)."""

from datetime import UTC, datetime
from pathlib import Path

import pytest

from keystone.ingest.parse import date_from_filename, parse_file

DATA = Path(__file__).resolve().parents[1] / "demo" / "data"


def test_date_from_filename() -> None:
    assert date_from_filename("2025-03-10_account_plan.docx") == datetime(2025, 3, 10, tzinfo=UTC)
    assert date_from_filename("account_plan.docx") is None


def test_word_sections_keep_headings() -> None:
    doc = parse_file(DATA / "2025-03-10_harbor_hotels_account_plan.docx")
    assert doc.title == "Account Plan: Harbor Hotels Group"
    sections = {s.heading: s.text for s in doc.sections}
    assert "Sara Khan is the account manager" in sections["Account team"]


def test_excel_rows_become_readable_facts() -> None:
    doc = parse_file(DATA / "2025-06-30_q2_customer_report.xlsx")
    text = " ".join(s.text for s in doc.sections)
    assert "Harbor Hotels Group, Account manager = Sara Khan" in text


def test_powerpoint_slides_are_pages() -> None:
    doc = parse_file(DATA / "2025-09-02_q3_finance_review.pptx")
    pages = {s.heading: s.pages for s in doc.sections}
    assert pages["Invoices"] == "p. 2"


def test_planted_contradiction_is_in_the_memo() -> None:
    doc = parse_file(DATA / "2026-09-15_account_reassignment_memo.docx")
    assert any("Bilal Ahmed is the account manager" in s.text for s in doc.sections)


def test_same_content_same_id(tmp_path: Path) -> None:
    source = DATA / "2026-09-15_account_reassignment_memo.docx"
    copy = tmp_path / "renamed.docx"
    copy.write_bytes(source.read_bytes())
    assert parse_file(copy).doc_id == parse_file(source).doc_id


def test_text_files(tmp_path: Path) -> None:
    path = tmp_path / "2026-01-02_note.txt"
    path.write_text("Ali works for Brightline.", encoding="utf-8")
    doc = parse_file(path)
    assert doc.sections[0].text == "Ali works for Brightline."


def test_rejects_unsupported_types(tmp_path: Path) -> None:
    path = tmp_path / "photo.jpg"
    path.write_bytes(b"x")
    with pytest.raises(ValueError, match="Unsupported file type"):
        parse_file(path)


@pytest.mark.slow
def test_pdf_with_page_numbers() -> None:
    doc = parse_file(DATA / "2025-01-15_company_overview.pdf")
    sections = {s.heading: s for s in doc.sections}
    assert "Omar Siddiqui is the Head of Sales" in sections["Leadership"].text
    assert sections["Leadership"].pages == "p. 1"
