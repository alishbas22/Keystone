"""Connector SDK (KAN-18): the standard interface every data source implements.

Pattern borrowed from the Airbyte CDK: a connector is one class with three methods.

    check()     -> CheckResult      can we connect with this configuration?
    discover()  -> list[Stream]     what collections are there (folders, tables, labels)?
    read()      -> Iterator[Document]  the content, as standard Document objects

Every connector emits the same `Document` model, so parsing, extraction and the graph never
need to know where the data came from.

Minimal example:

    class NotesConfig(BaseModel):
        notes: list[str]

    class NotesConnector(BaseConnector[NotesConfig]):
        manifest = ConnectorManifest(
            name="notes", version="0.1.0", description="In-memory notes."
        )
        config_model = NotesConfig

        def check(self) -> CheckResult:
            return CheckResult.success(f"{len(self.config.notes)} notes")

        def discover(self) -> list[Stream]:
            return [Stream(name="notes")]

        def read(self, stream=None, since=None):
            for i, note in enumerate(self.config.notes):
                yield Document(id=str(i), source=self.source_name, title=f"Note {i}", text=note)
"""

import hashlib
import re
from abc import ABC, abstractmethod
from collections.abc import Iterator
from datetime import datetime
from typing import Any, ClassVar, Generic, Self, TypeVar

from pydantic import BaseModel, ConfigDict, Field, model_validator

_CONNECTOR_NAME = re.compile(r"^[a-z][a-z0-9_-]*$")


class Document(BaseModel):
    """One item read from a source: a file, a database row, an email, a page."""

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1, description="Stable id within the source (path, row key).")
    source: str = Field(min_length=1, description="Connector instance, e.g. 'folder:sales'.")
    title: str
    file_name: str | None = Field(
        default=None, description="Original file name; its extension selects the parser."
    )
    content: bytes | None = Field(default=None, description="Raw file bytes (parsed later).")
    text: str | None = Field(default=None, description="Already-textual content.")
    modified_at: datetime | None = Field(default=None, description="When the source changed it.")
    author: str | None = None
    url: str | None = Field(default=None, description="Where a human can open the original.")
    business_function: str | None = Field(
        default=None, description="Suggested owner (Sales, Finance, HR, Operations)."
    )
    metadata: dict[str, str] = {}

    @model_validator(mode="after")
    def _exactly_one_body(self) -> Self:
        if (self.content is None) == (self.text is None):
            raise ValueError("a Document needs exactly one of `content` (bytes) or `text`")
        if self.content is not None and not self.file_name:
            raise ValueError("`file_name` is required with `content`, to choose the parser")
        return self

    @property
    def content_hash(self) -> str:
        """Fingerprint of the content: unchanged documents are not ingested twice."""
        data = self.content if self.content is not None else (self.text or "").encode("utf-8")
        return hashlib.sha256(data).hexdigest()[:16]


class Stream(BaseModel):
    """A collection a connector can read: a folder, a table, a mailbox label."""

    name: str
    description: str = ""
    document_count: int | None = None


class CheckResult(BaseModel):
    ok: bool
    message: str

    @classmethod
    def success(cls, message: str = "Connected") -> "CheckResult":
        return cls(ok=True, message=message)

    @classmethod
    def failure(cls, message: str) -> "CheckResult":
        return cls(ok=False, message=message)


class ConnectorManifest(BaseModel):
    """Who a connector is. Shown in the UI and used by the registry."""

    model_config = ConfigDict(frozen=True)

    name: str = Field(description="Unique short name, also the entry point name, e.g. 'folder'.")
    version: str
    description: str
    author: str = "Keystone contributors"
    supports_incremental: bool = Field(
        default=False, description="read(since=...) returns only documents changed after `since`."
    )

    @model_validator(mode="after")
    def _check_name(self) -> Self:
        if not _CONNECTOR_NAME.match(self.name):
            raise ValueError(f"connector name '{self.name}' must be lowercase, e.g. 'my-crm'")
        return self


ConfigT = TypeVar("ConfigT", bound=BaseModel)


class BaseConnector(ABC, Generic[ConfigT]):
    """Base class for all connectors. Subclasses set `manifest` and `config_model`."""

    manifest: ClassVar[ConnectorManifest]
    config_model: ClassVar[type[BaseModel]]

    def __init__(self, config: ConfigT | dict[str, Any], instance_name: str | None = None):
        self.config: ConfigT = (
            config if isinstance(config, BaseModel) else self.config_model.model_validate(config)  # type: ignore[assignment]
        )
        self.instance_name = instance_name or "default"

    @property
    def source_name(self) -> str:
        """Value for Document.source, e.g. 'folder:sales-drive'."""
        return f"{self.manifest.name}:{self.instance_name}"

    @classmethod
    def config_schema(cls) -> dict[str, Any]:
        """JSON schema of the configuration, so a UI can render a settings form."""
        return cls.config_model.model_json_schema()

    @abstractmethod
    def check(self) -> CheckResult:
        """Test the configuration (credentials, paths, network) without reading content."""

    @abstractmethod
    def discover(self) -> list[Stream]:
        """List the collections this source offers."""

    @abstractmethod
    def read(self, stream: str | None = None, since: datetime | None = None) -> Iterator[Document]:
        """Yield documents from one stream (or all). `since` asks for changes only, if supported."""
