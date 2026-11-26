"""Gmail connector (KAN-21): emails and their attachments, read-only.

Each email is read in raw form and handed on as an .eml file, so the parser keeps its sender and
sent date (dates the facts) and its text. Supported attachments (PDF, Word, Excel...) become
documents of their own. Streams are Gmail labels; a label named Sales, Finance, HR or Operations
sets the business function. `query` uses Gmail search syntax (e.g. "newer_than:90d").
"""

import base64
from collections.abc import Iterator
from datetime import UTC, datetime
from email import policy
from email.parser import BytesParser
from email.utils import getaddresses
from pathlib import PurePosixPath
from typing import Any

from pydantic import Field

from keystone.connectors.base import (
    BaseConnector,
    CheckResult,
    ConnectorManifest,
    Document,
    Stream,
)
from keystone.connectors.google._common import function_from_names
from keystone.connectors.google.auth import GoogleAuth, GoogleAuthRequired, build_service
from keystone.ingest.formats import SUPPORTED_SUFFIXES

SYSTEM_STREAMS = {"INBOX", "SENT", "IMPORTANT", "STARRED"}


class GmailConfig(GoogleAuth):
    query: str = Field(
        default="newer_than:365d", description="Gmail search, e.g. 'from:@acme.com'."
    )
    max_messages: int = Field(default=500, ge=1, le=10000)
    include_attachments: bool = Field(default=True, description="Read supported attachments too.")
    max_attachment_mb: float = Field(default=20, gt=0)
    business_function: str | None = Field(
        default=None, description="Owner when no label names a business function."
    )


class GmailConnector(BaseConnector[GmailConfig]):
    manifest = ConnectorManifest(
        name="gmail",
        version="0.1.0",
        description="Gmail messages and their attachments (read-only).",
        supports_incremental=True,
    )
    config_model = GmailConfig

    def __init__(
        self,
        config: GmailConfig | dict[str, Any],
        instance_name: str | None = None,
        service: Any = None,
    ):
        super().__init__(config, instance_name)
        self._service = service
        self._labels: dict[str, str] | None = None  # label id -> name

    @property
    def service(self) -> Any:
        if self._service is None:
            self._service = build_service("gmail", "v1", self.config)
        return self._service

    def labels(self) -> dict[str, str]:
        if self._labels is None:
            found = self.service.users().labels().list(userId="me").execute().get("labels", [])
            self._labels = {label["id"]: label["name"] for label in found}
        return self._labels

    # --- SDK methods ---------------------------------------------------------------------------

    def check(self) -> CheckResult:
        try:
            profile = self.service.users().getProfile(userId="me").execute()
        except GoogleAuthRequired as exc:
            return CheckResult.failure(str(exc))
        except Exception as exc:
            return CheckResult.failure(f"Cannot reach Gmail: {type(exc).__name__}: {exc}")
        total = profile.get("messagesTotal", "?")
        return CheckResult.success(f"Connected to {profile.get('emailAddress')} ({total} messages)")

    def discover(self) -> list[Stream]:
        streams = []
        found = self.service.users().labels().list(userId="me").execute().get("labels", [])
        for label in sorted(found, key=lambda lb: lb["name"]):
            if label.get("type") == "user" or label["id"] in SYSTEM_STREAMS:
                streams.append(Stream(name=label["name"], description=f"Gmail label {label['id']}"))
        return streams

    def read(self, stream: str | None = None, since: datetime | None = None) -> Iterator[Document]:
        label_ids = None
        if stream is not None:
            by_name = {name: label_id for label_id, name in self.labels().items()}
            if stream not in by_name:
                raise ValueError(f"no Gmail label named '{stream}'")
            label_ids = [by_name[stream]]
        query = self.config.query
        if since is not None:
            query = f"{query} after:{int(since.timestamp())}".strip()
        for message_id in self._message_ids(query, label_ids):
            message = (
                self.service.users()
                .messages()
                .get(userId="me", id=message_id, format="raw")
                .execute()
            )
            yield from self._documents(message)

    # --- helpers -----------------------------------------------------------------------------

    def _message_ids(self, query: str, label_ids: list[str] | None) -> Iterator[str]:
        count, token = 0, None
        while count < self.config.max_messages:
            page = (
                self.service.users()
                .messages()
                .list(
                    userId="me",
                    q=query,
                    labelIds=label_ids,
                    maxResults=min(500, self.config.max_messages - count),
                    pageToken=token,
                )
                .execute()
            )
            for item in page.get("messages", []):
                if count >= self.config.max_messages:
                    return
                count += 1
                yield item["id"]
            token = page.get("nextPageToken")
            if not token:
                return

    def _documents(self, message: dict[str, Any]) -> Iterator[Document]:
        raw = message["raw"]
        data = base64.urlsafe_b64decode(raw + "=" * (-len(raw) % 4))
        parsed = BytesParser(policy=policy.default).parsebytes(data)
        subject = str(parsed.get("Subject", "")) or "(no subject)"
        senders = getaddresses([str(parsed.get("From", ""))])
        author = next((name or address for name, address in senders if name or address), None)
        sent = datetime.fromtimestamp(int(message.get("internalDate", 0)) / 1000, tz=UTC)
        label_names = [self.labels().get(i, i) for i in message.get("labelIds", [])]
        thread = message.get("threadId", message["id"])
        metadata = {"thread_id": str(thread), "labels": ", ".join(label_names)}

        def document(doc_id: str, title: str, file_name: str, content: bytes) -> Document:
            return Document(
                id=doc_id,
                source=self.source_name,
                title=title,
                file_name=file_name,
                content=content,
                modified_at=sent,
                author=author,
                url=f"https://mail.google.com/mail/u/0/#all/{thread}",
                business_function=function_from_names(label_names, self.config.business_function),
                metadata=metadata,
            )

        yield document(f"gmail/{message['id']}", subject, f"{message['id']}.eml", data)
        if not self.config.include_attachments:
            return
        limit = self.config.max_attachment_mb * 1024 * 1024
        for part in parsed.iter_attachments():
            name = part.get_filename()
            if not name or PurePosixPath(name).suffix.lower() not in SUPPORTED_SUFFIXES:
                continue
            payload = part.get_payload(decode=True)
            if not isinstance(payload, bytes) or not payload or len(payload) > limit:
                continue
            title = f"{PurePosixPath(name).stem} (attachment: {subject})"
            yield document(f"gmail/{message['id']}/{name}", title, name, payload)
