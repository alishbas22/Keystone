"""Folder connector (KAN-20): every supported file in a folder and its subfolders.

Used for document uploads and shared drives synced to disk. A file inside a subfolder named
after a business function (e.g. `Finance/`) is suggested as belonging to that function.
"""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

from pydantic import BaseModel, Field

from keystone.connectors.base import (
    BaseConnector,
    CheckResult,
    ConnectorManifest,
    Document,
    Stream,
)
from keystone.ingest.formats import SUPPORTED_SUFFIXES

ROOT_STREAM = "."
BUSINESS_FUNCTIONS = ("Sales", "Finance", "HR", "Operations")


class FolderConfig(BaseModel):
    path: Path = Field(description="Folder to read.")
    recursive: bool = Field(default=True, description="Also read subfolders.")
    extensions: list[str] = Field(
        default=SUPPORTED_SUFFIXES, description="File types to read, e.g. ['.pdf', '.docx']."
    )
    max_file_mb: float = Field(default=50, gt=0, description="Skip files larger than this.")
    business_function: str | None = Field(
        default=None, description="Owner of these files when no subfolder name says otherwise."
    )


class FolderConnector(BaseConnector[FolderConfig]):
    manifest = ConnectorManifest(
        name="folder",
        version="0.1.0",
        description="Documents in a local or synced folder (PDF, Word, Excel, PowerPoint, email…).",
        supports_incremental=True,
    )
    config_model = FolderConfig

    @property
    def root(self) -> Path:
        return self.config.path.expanduser().resolve()

    def check(self) -> CheckResult:
        if not self.root.exists():
            return CheckResult.failure(f"Folder not found: {self.root}")
        if not self.root.is_dir():
            return CheckResult.failure(f"Not a folder: {self.root}")
        count = sum(1 for _ in self._files(self.root))
        return CheckResult.success(f"{count} supported files in {self.root}")

    def discover(self) -> list[Stream]:
        streams = [
            Stream(
                name=ROOT_STREAM,
                description="The whole folder",
                document_count=sum(1 for _ in self._files(self.root)),
            )
        ]
        for sub in sorted(p for p in self.root.iterdir() if p.is_dir() and not _hidden(p.name)):
            streams.append(Stream(name=sub.name, document_count=sum(1 for _ in self._files(sub))))
        return streams

    def read(self, stream: str | None = None, since: datetime | None = None) -> Iterator[Document]:
        base = self.root if stream in (None, ROOT_STREAM) else (self.root / str(stream)).resolve()
        if not base.is_relative_to(self.root):
            raise ValueError(f"stream '{stream}' is outside the configured folder")
        for path in self._files(base):
            stat = path.stat()
            modified = datetime.fromtimestamp(stat.st_mtime, tz=UTC)
            if since is not None and modified <= since:
                continue
            relative = path.relative_to(self.root)
            yield Document(
                id=relative.as_posix(),
                source=self.source_name,
                title=path.stem.replace("_", " "),
                file_name=path.name,
                content=path.read_bytes(),
                modified_at=modified,
                url=path.as_uri(),
                business_function=self._function_for(relative),
                metadata={"folder": relative.parent.as_posix(), "size_bytes": str(stat.st_size)},
            )

    def _files(self, base: Path) -> Iterator[Path]:
        allowed = {
            e.lower() if e.startswith(".") else f".{e.lower()}" for e in self.config.extensions
        }
        max_bytes = self.config.max_file_mb * 1024 * 1024
        candidates = base.rglob("*") if self.config.recursive else base.glob("*")
        for path in sorted(candidates):
            relative = path.relative_to(self.root).parts
            if any(_hidden(part) for part in relative):
                continue
            if (
                path.is_file()
                and path.suffix.lower() in allowed
                and path.stat().st_size <= max_bytes
            ):
                yield path

    def _function_for(self, relative: Path) -> str | None:
        for part in relative.parts[:-1]:
            for function in BUSINESS_FUNCTIONS:
                if part.lower() == function.lower():
                    return function
        return self.config.business_function


def _hidden(name: str) -> bool:
    """Hidden files/folders and Office lock files such as '~$report.docx'."""
    return name.startswith((".", "~$", "__"))
