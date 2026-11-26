"""KAN-21: Gmail and Google Drive connectors, tested with stand-in Google API services."""

import base64
import re
from datetime import UTC, datetime
from email.message import EmailMessage
from pathlib import Path
from typing import Any

import pytest

from keystone.connectors import available_connectors
from keystone.connectors.google.drive import DriveConnector
from keystone.connectors.google.gmail import GmailConnector
from keystone.connectors.testing import check_connector
from keystone.ingest.parse import parse_document

MEMO = next(
    (Path(__file__).resolve().parents[1] / "demo" / "data").rglob("*reassignment_memo.docx")
)


class Exec:
    def __init__(self, value: Any) -> None:
        self.value = value

    def execute(self) -> Any:
        return self.value


# --- A stand-in Google Drive -------------------------------------------------------------------

GDOC = "application/vnd.google-apps.document"
FOLDER = "application/vnd.google-apps.folder"
TREE: dict[str, list[dict[str, Any]]] = {
    "root": [
        {"id": "f-sales", "name": "Sales", "mimeType": FOLDER},
        {"id": "f-archive", "name": "Archive", "mimeType": FOLDER},
        {"id": "readme", "name": "readme.md", "mimeType": "text/markdown", "size": "20",
         "modifiedTime": "2026-09-01T10:00:00Z", "owners": [{"displayName": "Amina Yusuf"}]},
    ],
    "f-sales": [
        {"id": "plan", "name": "Account plan", "mimeType": GDOC, "modifiedTime": "2026-09-15T09:00:00.000Z",
         "webViewLink": "https://docs.google.com/document/d/plan", "lastModifyingUser": {"displayName": "Omar Siddiqui"}},
        {"id": "photo", "name": "team.jpg", "mimeType": "image/jpeg", "size": "10", "modifiedTime": "2026-09-01T00:00:00Z"},
        {"id": "big", "name": "huge.pdf", "mimeType": "application/pdf", "size": str(200 * 1024 * 1024),
         "modifiedTime": "2026-09-01T00:00:00Z"},
    ],
    "f-archive": [
        {"id": "old", "name": "notes.md", "mimeType": "text/markdown", "size": "10", "modifiedTime": "2024-01-01T00:00:00Z"},
    ],
}  # fmt: skip


class FakeDrive:
    def __init__(self, page_size: int = 2) -> None:
        self.page_size = page_size
        self.exports: list[str] = []

    def files(self) -> "FakeDrive":
        return self

    def about(self) -> "FakeDrive":
        return self

    def get(self, fields: str) -> Exec:
        return Exec({"user": {"displayName": "Amina", "emailAddress": "amina@brightline.example"}})

    def list(self, q: str, pageToken: str | None = None, **_: Any) -> Exec:
        parent = re.match(r"'([^']+)' in parents", q).group(1)  # type: ignore[union-attr]
        items = TREE.get(parent, [])
        start = int(pageToken or 0)
        page: dict[str, Any] = {"files": items[start : start + self.page_size]}
        if start + self.page_size < len(items):
            page["nextPageToken"] = str(start + self.page_size)
        return Exec(page)

    def get_media(self, fileId: str) -> Exec:
        return Exec(
            b"# Readme\n\nHarbor Hotels Group is a customer."
            if fileId == "readme"
            else b"# Notes\n\nOld."
        )

    def export_media(self, fileId: str, mimeType: str) -> Exec:
        self.exports.append(mimeType)
        return Exec(MEMO.read_bytes())


def drive(**config: Any) -> tuple[DriveConnector, FakeDrive]:
    fake = FakeDrive()
    return DriveConnector({"business_function": "Operations", **config}, "test", service=fake), fake


def test_drive_follows_the_connector_contract() -> None:
    connector, _ = drive()
    documents = check_connector(connector)
    assert sorted(d.id for d in documents) == ["old", "plan", "readme"]


def test_drive_exports_google_docs_and_skips_unsupported_files() -> None:
    connector, fake = drive()
    docs = {d.id: d for d in connector.read()}
    plan = docs["plan"]
    assert plan.file_name == "Account plan.docx"
    assert fake.exports == [
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    ]
    assert (plan.author, plan.url) == ("Omar Siddiqui", "https://docs.google.com/document/d/plan")
    assert plan.modified_at == datetime(2026, 9, 15, 9, 0, tzinfo=UTC)
    assert plan.metadata["drive_path"] == "/Sales/Account plan"
    assert "photo" not in docs and "big" not in docs  # unsupported type, too large
    assert parse_document(plan).sections  # the exported Word file is readable


def test_drive_business_function_from_folder_names() -> None:
    connector, _ = drive()
    functions = {d.id: d.business_function for d in connector.read()}
    assert functions == {"plan": "Sales", "readme": "Operations", "old": "Operations"}


def test_drive_streams_and_incremental_reads() -> None:
    connector, _ = drive()
    assert [s.name for s in connector.discover()] == [".", "Sales", "Archive"]
    assert [d.id for d in connector.read(stream="Sales")] == ["plan"]
    since = datetime(2026, 1, 1, tzinfo=UTC)
    assert sorted(d.id for d in connector.read(since=since)) == ["plan", "readme"]
    with pytest.raises(ValueError, match="no folder named 'Legal'"):
        list(connector.read(stream="Legal"))


def test_drive_non_recursive() -> None:
    connector, _ = drive(recursive=False)
    assert [d.id for d in connector.read()] == ["readme"]


# --- A stand-in Gmail ----------------------------------------------------------------------------


def raw_email(
    subject: str, sender: str, date: str, body: str, attachments: dict[str, bytes] | None = None
) -> str:
    msg = EmailMessage()
    msg["From"], msg["To"], msg["Date"], msg["Subject"] = (
        sender,
        "sales@brightline.example",
        date,
        subject,
    )
    msg.set_content(body)
    for name, data in (attachments or {}).items():
        maintype, subtype = ("text", "markdown") if name.endswith(".md") else ("image", "jpeg")
        msg.add_attachment(data, maintype=maintype, subtype=subtype, filename=name)
    return base64.urlsafe_b64encode(bytes(msg)).decode().rstrip("=")


MESSAGES = {
    "m1": {"id": "m1", "threadId": "t1", "labelIds": ["INBOX", "Label_sales"], "internalDate": "1789462800000",
           "raw": raw_email("Harbor Hotels account", "Omar Siddiqui <omar@brightline.example>",
                            "Tue, 15 Sep 2026 09:30:00 +0100", "Bilal Ahmed now manages the Harbor Hotels Group account.",
                            {"handover.md": b"# Handover\n\nBilal Ahmed takes over.", "photo.jpg": b"\xff\xd8"})},
    "m2": {"id": "m2", "threadId": "t2", "labelIds": ["INBOX"], "internalDate": "1789549200000",
           "raw": raw_email("Lunch", "Sara Khan <sara@brightline.example>", "Wed, 16 Sep 2026 09:00:00 +0100", "Lunch at 1?")},
}  # fmt: skip
LABELS = [
    {"id": "INBOX", "name": "INBOX", "type": "system"},
    {"id": "CATEGORY_PROMOTIONS", "name": "CATEGORY_PROMOTIONS", "type": "system"},
    {"id": "Label_sales", "name": "Sales", "type": "user"},
]


class FakeGmail:
    def __init__(self) -> None:
        self.queries: list[str] = []
        self._section = ""

    def users(self) -> "FakeGmail":
        return self

    def labels(self) -> "FakeGmail":
        self._section = "labels"
        return self

    def messages(self) -> "FakeGmail":
        self._section = "messages"
        return self

    def getProfile(self, userId: str) -> Exec:
        return Exec({"emailAddress": "sales@brightline.example", "messagesTotal": 2})

    def list(
        self, userId: str, q: str = "", labelIds: Any = None, pageToken: str | None = None, **_: Any
    ) -> Exec:
        if self._section == "labels":
            return Exec({"labels": LABELS})
        self.queries.append(q)
        ids = [m for m in MESSAGES if not labelIds or set(labelIds) <= set(MESSAGES[m]["labelIds"])]
        start = int(pageToken or 0)
        page: dict[str, Any] = {"messages": [{"id": i} for i in ids[start : start + 1]]}
        if start + 1 < len(ids):
            page["nextPageToken"] = str(start + 1)
        return Exec(page)

    def get(self, userId: str, id: str, format: str) -> Exec:
        assert format == "raw"
        return Exec(MESSAGES[id])


def gmail(**config: Any) -> tuple[GmailConnector, FakeGmail]:
    fake = FakeGmail()
    return GmailConnector({"business_function": "Operations", **config}, "test", service=fake), fake


def test_gmail_follows_the_connector_contract() -> None:
    connector, _ = gmail()
    documents = check_connector(connector)
    assert [d.id for d in documents] == ["gmail/m1", "gmail/m1/handover.md", "gmail/m2"]


def test_gmail_messages_keep_sender_date_and_label_function() -> None:
    connector, _ = gmail()
    docs = {d.id: d for d in connector.read()}
    email = docs["gmail/m1"]
    assert (email.title, email.author, email.file_name) == (
        "Harbor Hotels account",
        "Omar Siddiqui",
        "m1.eml",
    )
    assert email.business_function == "Sales"  # from the Gmail label
    assert docs["gmail/m2"].business_function == "Operations"  # default
    assert email.url == "https://mail.google.com/mail/u/0/#all/t1"
    parsed = parse_document(email)
    assert parsed.written_at == datetime(2026, 9, 15, 8, 30, tzinfo=UTC)  # the email's own date
    assert "Bilal Ahmed now manages" in " ".join(s.text for s in parsed.sections)


def test_gmail_attachments_become_documents() -> None:
    connector, _ = gmail()
    docs = {d.id: d for d in connector.read()}
    attachment = docs["gmail/m1/handover.md"]
    assert attachment.file_name == "handover.md" and attachment.business_function == "Sales"
    assert parse_document(attachment).sections[0].text == "Bilal Ahmed takes over."
    assert "gmail/m1/photo.jpg" not in docs  # unsupported type
    no_attachments, _ = gmail(include_attachments=False)
    assert [d.id for d in no_attachments.read()] == ["gmail/m1", "gmail/m2"]


def test_gmail_labels_queries_and_limits() -> None:
    connector, fake = gmail(query="newer_than:30d")
    assert [s.name for s in connector.discover()] == ["INBOX", "Sales"]  # user labels + inbox
    assert [d.id for d in connector.read(stream="Sales")] == ["gmail/m1", "gmail/m1/handover.md"]
    list(connector.read(since=datetime(2026, 9, 1, tzinfo=UTC)))
    assert (
        fake.queries[-1]
        == f"newer_than:30d after:{int(datetime(2026, 9, 1, tzinfo=UTC).timestamp())}"
    )
    capped, _ = gmail(max_messages=1, include_attachments=False)
    assert [d.id for d in capped.read()] == ["gmail/m1"]
    with pytest.raises(ValueError, match="no Gmail label named 'Legal'"):
        list(connector.read(stream="Legal"))


# --- Sign-in and registration ------------------------------------------------------------------


def test_not_signed_in_is_reported_clearly(tmp_path: Path) -> None:
    for cls in (DriveConnector, GmailConnector):
        result = cls({"token_file": tmp_path / "missing.json"}).check()
        assert not result.ok and "Not signed in to Google yet" in result.message


def test_registered_as_plugins() -> None:
    assert {"google-drive", "gmail"} <= set(available_connectors())
