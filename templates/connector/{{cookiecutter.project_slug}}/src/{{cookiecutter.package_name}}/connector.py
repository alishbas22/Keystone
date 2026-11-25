"""{{ cookiecutter.display_name }} connector for Keystone.

This example reads records from a JSON file so it works out of the box. To connect your real
system, replace `_fetch_records` (and the config fields) with calls to its API or database.
Everything else (check, discover, read) usually needs only small changes.
"""

import json
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from keystone.connectors import (
    BaseConnector,
    CheckResult,
    ConnectorManifest,
    Document,
    Stream,
)
from pydantic import BaseModel, Field, SecretStr


class {{ cookiecutter.class_name }}Config(BaseModel):
    """Settings a user fills in. Keystone shows this as a form (see config_schema())."""

    # TODO: replace with what your system needs, e.g. base_url and an API key.
    data_file: Path = Field(description="JSON file with a list of records (example source).")
    api_key: SecretStr | None = Field(default=None, description="Example secret, never logged.")


class {{ cookiecutter.class_name }}(BaseConnector[{{ cookiecutter.class_name }}Config]):
    manifest = ConnectorManifest(
        name="{{ cookiecutter.connector_name }}",  # must equal the entry point name
        version="0.1.0",
        description="{{ cookiecutter.description }}",
        author="{{ cookiecutter.author }}",
        supports_incremental=True,
    )
    config_model = {{ cookiecutter.class_name }}Config

    # 1. check(): can we connect? Report problems instead of raising.
    def check(self) -> CheckResult:
        try:
            records = self._fetch_records()
        except Exception as exc:
            return CheckResult.failure(f"Cannot read {{ cookiecutter.display_name }}: {exc}")
        return CheckResult.success(f"{len(records)} records available")

    # 2. discover(): which collections exist? (e.g. one stream per object type or folder)
    def discover(self) -> list[Stream]:
        counts: dict[str, int] = {}
        for record in self._fetch_records():
            kind = str(record.get("kind", "records"))
            counts[kind] = counts.get(kind, 0) + 1
        return [Stream(name=kind, document_count=n) for kind, n in sorted(counts.items())]

    # 3. read(): yield one Document per item. Keep ids stable so re-syncs update, not duplicate.
    def read(self, stream: str | None = None, since: datetime | None = None) -> Iterator[Document]:
        for record in self._fetch_records():
            kind = str(record.get("kind", "records"))
            if stream is not None and kind != stream:
                continue
            updated = _parse_time(record.get("updated_at"))
            if since is not None and updated is not None and updated <= since:
                continue
            yield Document(
                id=f"{kind}/{record['id']}",
                source=self.source_name,
                title=str(record.get("title", record["id"])),
                text=str(record["text"]),  # or content=bytes + file_name="x.pdf" for files
                modified_at=updated,
                author=record.get("author"),
                url=record.get("url"),
                business_function="{{ cookiecutter.business_function }}",
            )

    # TODO: replace with calls to your system (requests/httpx, an SDK, a database driver...).
    def _fetch_records(self) -> list[dict[str, Any]]:
        data = json.loads(Path(self.config.data_file).read_text(encoding="utf-8"))
        if not isinstance(data, list):
            raise ValueError("expected a JSON list of records")
        return data


def _parse_time(value: Any) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(str(value))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
