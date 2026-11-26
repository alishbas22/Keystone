"""Google Drive connector (KAN-21): files and Google Docs/Sheets/Slides, read-only.

Walks folders from `folder_id` (default: My Drive). Google Docs, Sheets and Slides are exported
as Word, Excel and PowerPoint so Docling can read them; other files are downloaded if their type
is supported. A file inside a folder named Sales, Finance, HR or Operations gets that business
function. Streams are the top-level subfolders.
"""

from collections.abc import Iterator
from datetime import datetime
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
from keystone.connectors.google._common import function_from_names, parse_rfc3339
from keystone.connectors.google.auth import GoogleAuth, GoogleAuthRequired, build_service
from keystone.ingest.formats import SUPPORTED_SUFFIXES

FOLDER = "application/vnd.google-apps.folder"
# Google's own formats -> export format and the extension that selects Keystone's parser.
EXPORTS = {
    "application/vnd.google-apps.document": (
        "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        ".docx",
    ),
    "application/vnd.google-apps.spreadsheet": (
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        ".xlsx",
    ),
    "application/vnd.google-apps.presentation": (
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
        ".pptx",
    ),
}
FIELDS = (
    "nextPageToken, files(id, name, mimeType, modifiedTime, size, webViewLink, "
    "owners(displayName), lastModifyingUser(displayName))"
)
ROOT_STREAM = "."


class DriveConfig(GoogleAuth):
    folder_id: str = Field(default="root", description="Folder to read ('root' = My Drive).")
    recursive: bool = Field(default=True, description="Also read subfolders.")
    extensions: list[str] = Field(default=SUPPORTED_SUFFIXES, description="File types to read.")
    max_file_mb: float = Field(default=50, gt=0)
    include_shared_drives: bool = Field(default=False, description="Also list shared drives.")
    business_function: str | None = Field(
        default=None, description="Owner when no folder name says otherwise."
    )


class DriveConnector(BaseConnector[DriveConfig]):
    manifest = ConnectorManifest(
        name="google-drive",
        version="0.1.0",
        description="Google Drive files and Google Docs, Sheets and Slides (read-only).",
        supports_incremental=True,
    )
    config_model = DriveConfig

    def __init__(
        self,
        config: DriveConfig | dict[str, Any],
        instance_name: str | None = None,
        service: Any = None,
    ):
        super().__init__(config, instance_name)
        self._service = service  # injected in tests; otherwise built on first use

    @property
    def service(self) -> Any:
        if self._service is None:
            self._service = build_service("drive", "v3", self.config)
        return self._service

    # --- SDK methods ---------------------------------------------------------------------------

    def check(self) -> CheckResult:
        try:
            about = self.service.about().get(fields="user(displayName, emailAddress)").execute()
        except GoogleAuthRequired as exc:
            return CheckResult.failure(str(exc))
        except Exception as exc:
            return CheckResult.failure(f"Cannot reach Google Drive: {type(exc).__name__}: {exc}")
        user = about.get("user", {})
        return CheckResult.success(f"Connected as {user.get('emailAddress', 'unknown')}")

    def discover(self) -> list[Stream]:
        streams = [Stream(name=ROOT_STREAM, description="The whole folder")]
        for item in self._children(self.config.folder_id):
            if item["mimeType"] == FOLDER:
                streams.append(Stream(name=item["name"], description=f"Drive folder {item['id']}"))
        return streams

    def read(self, stream: str | None = None, since: datetime | None = None) -> Iterator[Document]:
        start, path = self.config.folder_id, PurePosixPath("/")
        if stream not in (None, ROOT_STREAM):
            match = next(
                (
                    i
                    for i in self._children(start)
                    if i["mimeType"] == FOLDER and i["name"] == stream
                ),
                None,
            )
            if match is None:
                raise ValueError(f"no folder named '{stream}' in the configured Drive folder")
            start, path = match["id"], path / match["name"]
        yield from self._walk(start, path, since)

    # --- helpers -----------------------------------------------------------------------------

    def _children(self, folder_id: str) -> Iterator[dict[str, Any]]:
        shared = self.config.include_shared_drives
        token = None
        while True:
            page = (
                self.service.files()
                .list(
                    q=f"'{folder_id}' in parents and trashed = false",
                    fields=FIELDS,
                    pageSize=100,
                    pageToken=token,
                    supportsAllDrives=shared,
                    includeItemsFromAllDrives=shared,
                )
                .execute()
            )
            yield from page.get("files", [])
            token = page.get("nextPageToken")
            if not token:
                return

    def _walk(
        self, folder_id: str, path: PurePosixPath, since: datetime | None
    ) -> Iterator[Document]:
        allowed = {
            e.lower() if e.startswith(".") else f".{e.lower()}" for e in self.config.extensions
        }
        subfolders = []
        for item in sorted(self._children(folder_id), key=lambda i: i["name"]):
            mime = item["mimeType"]
            if mime == FOLDER:
                subfolders.append(item)
                continue
            modified = parse_rfc3339(item.get("modifiedTime"))
            if since is not None and modified is not None and modified <= since:
                continue
            document = self._document(item, path, modified, allowed)
            if document is not None:
                yield document
        if self.config.recursive:
            for folder in subfolders:
                yield from self._walk(folder["id"], path / folder["name"], since)

    def _document(
        self,
        item: dict[str, Any],
        path: PurePosixPath,
        modified: datetime | None,
        allowed: set[str],
    ) -> Document | None:
        mime, name = item["mimeType"], item["name"]
        if mime in EXPORTS:
            export_mime, suffix = EXPORTS[mime]
            if suffix not in allowed:
                return None
            content = (
                self.service.files().export_media(fileId=item["id"], mimeType=export_mime).execute()
            )
            file_name = f"{name}{suffix}"
        else:
            suffix = PurePosixPath(name).suffix.lower()
            if (
                suffix not in allowed
                or int(item.get("size", 0)) > self.config.max_file_mb * 1024 * 1024
            ):
                return None
            content = self.service.files().get_media(fileId=item["id"]).execute()
            file_name = name
        author = (item.get("lastModifyingUser") or {}).get("displayName") or next(
            (o.get("displayName") for o in item.get("owners", []) if o.get("displayName")), None
        )
        return Document(
            id=item["id"],
            source=self.source_name,
            title=PurePosixPath(file_name).stem.replace("_", " "),
            file_name=file_name,
            content=bytes(content),
            modified_at=modified,
            author=author,
            url=item.get("webViewLink"),
            business_function=function_from_names(path.parts, self.config.business_function),
            metadata={"drive_path": str(path / name), "mime_type": mime},
        )
