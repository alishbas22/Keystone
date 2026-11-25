"""KAN-19 (minimal): Docling parsing of the sample documents. No LLM or network needed,
except the PDF test, which needs Docling's layout models (downloaded on first use)."""

from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path

import pytest

from keystone.connectors import Document
from keystone.ingest.parse import (
    ParseError,
    date_from_filename,
    parse_bytes,
    parse_document,
    parse_file,
)

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
    with pytest.raises(ParseError, match="Unsupported file type"):
        parse_file(path)


def make_email() -> bytes:
    msg = EmailMessage()
    msg["From"] = "Omar Siddiqui <omar@brightline.example>"
    msg["To"] = "sales@brightline.example"
    msg["Date"] = "Tue, 15 Sep 2026 09:30:00 +0100"
    msg["Subject"] = "Harbor Hotels account"
    msg.set_content("Hi team,\n\nBilal Ahmed now manages the Harbor Hotels Group account.\n")
    return bytes(msg)


def test_email_uses_sender_and_sent_date() -> None:
    doc = parse_bytes("handover.eml", make_email())
    assert doc.title == "Harbor Hotels account"
    assert doc.author == "Omar Siddiqui"
    assert doc.written_at == datetime(2026, 9, 15, 8, 30, tzinfo=UTC)
    text = " ".join(s.text for s in doc.sections)
    assert "Bilal Ahmed now manages the Harbor Hotels Group account." in text


def test_csv_rows() -> None:
    data = b"customer,account_manager\nHarbor Hotels Group,Sara Khan\n"
    doc = parse_bytes("managers.csv", data)
    assert "Harbor Hotels Group" in " ".join(s.text for s in doc.sections)


def test_markdown_headings_become_sections() -> None:
    data = b"# Handbook\n\n## Leave\n\nStaff get 25 days.\n\n## Expenses\n\nKeep receipts.\n"
    doc = parse_bytes("handbook.md", data)
    assert {s.heading: s.text for s in doc.sections} == {
        "Leave": "Staff get 25 days.",
        "Expenses": "Keep receipts.",
    }


def test_connector_document_with_file_bytes() -> None:
    source = DATA / "2026-09-15_account_reassignment_memo.docx"
    document = Document(
        id="drive/123",
        source="folder:test",
        title="Reassignment memo",
        file_name=source.name,
        content=source.read_bytes(),
        author="Omar Siddiqui",
        modified_at=datetime(2026, 9, 15, tzinfo=UTC),
    )
    parsed = parse_document(document)
    assert parsed.doc_id == document.content_hash
    assert parsed.author == "Omar Siddiqui"
    assert parsed.written_at == datetime(2026, 9, 15, tzinfo=UTC)
    assert any("Bilal Ahmed" in s.text for s in parsed.sections)


def test_connector_document_with_text() -> None:
    document = Document(
        id="orders/PO-1042",
        source="postgres:sales",
        title="Order PO-1042",
        text="Order PO-1042 was placed by Harbor Hotels Group.",
    )
    parsed = parse_document(document)
    assert parsed.title == "Order PO-1042"
    assert parsed.sections[0].text == "Order PO-1042 was placed by Harbor Hotels Group."


def test_email_date_beats_connector_date() -> None:
    document = Document(
        id="m1",
        source="folder:x",
        title="mail",
        file_name="m1.eml",
        content=make_email(),
        modified_at=datetime(2027, 1, 1, tzinfo=UTC),  # e.g. when the file was copied
    )
    assert parse_document(document).written_at == datetime(2026, 9, 15, 8, 30, tzinfo=UTC)


def test_empty_file_is_rejected() -> None:
    with pytest.raises(ParseError, match="empty"):
        parse_bytes("blank.docx", b"")


def test_corrupt_file_gives_a_readable_error() -> None:
    with pytest.raises(ParseError, match="Could not read broken.docx"):
        parse_bytes("broken.docx", b"this is not a word file")


@pytest.mark.slow
def test_pdf_with_page_numbers() -> None:
    doc = parse_file(DATA / "2025-01-15_company_overview.pdf")
    sections = {s.heading: s for s in doc.sections}
    assert "Omar Siddiqui is the Head of Sales" in sections["Leadership"].text
    assert sections["Leadership"].pages == "p. 1"
