"""KAN-20: folder connector."""

import os
from datetime import UTC, datetime
from pathlib import Path

import pytest

from keystone.connectors import create_connector
from keystone.connectors.folder import FolderConnector
from keystone.ingest.parse import parse_document


@pytest.fixture
def drive(tmp_path: Path) -> Path:
    (tmp_path / "Finance").mkdir()
    (tmp_path / "Finance" / "budget.md").write_text("# Budget\n\nTravel: 5000", encoding="utf-8")
    (tmp_path / "Projects" / "Harbor").mkdir(parents=True)
    (tmp_path / "Projects" / "Harbor" / "notes.txt").write_text("Sara manages Harbor.", "utf-8")
    (tmp_path / "readme.md").write_text("# Shared drive", encoding="utf-8")
    (tmp_path / "photo.jpg").write_bytes(b"\xff\xd8")  # unsupported type
    (tmp_path / "~$budget.docx").write_bytes(b"lock")  # Office lock file
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config.md").write_text("hidden", encoding="utf-8")
    return tmp_path


def ids(connector: FolderConnector, **kwargs: object) -> list[str]:
    return [d.id for d in connector.read(**kwargs)]  # type: ignore[arg-type]


def test_reads_supported_files_only(drive: Path) -> None:
    connector = FolderConnector({"path": drive}, "shared")
    assert ids(connector) == ["Finance/budget.md", "Projects/Harbor/notes.txt", "readme.md"]


def test_document_fields(drive: Path) -> None:
    doc = next(d for d in FolderConnector({"path": drive}, "shared").read() if d.id == "readme.md")
    assert doc.source == "folder:shared"
    assert doc.file_name == "readme.md"
    assert doc.content == b"# Shared drive"
    assert doc.modified_at is not None and doc.modified_at.tzinfo is UTC
    assert doc.url is not None and doc.url.startswith("file:")


def test_business_function_from_folder_name(drive: Path) -> None:
    connector = FolderConnector({"path": drive, "business_function": "Operations"})
    functions = {d.id: d.business_function for d in connector.read()}
    assert functions["Finance/budget.md"] == "Finance"
    assert functions["readme.md"] == "Operations"  # default from config


def test_non_recursive(drive: Path) -> None:
    assert ids(FolderConnector({"path": drive, "recursive": False})) == ["readme.md"]


def test_extension_filter_and_size_limit(drive: Path) -> None:
    assert ids(FolderConnector({"path": drive, "extensions": ["txt"]})) == [
        "Projects/Harbor/notes.txt"
    ]
    (drive / "big.md").write_bytes(b"x" * 2_000_000)
    assert "big.md" not in ids(FolderConnector({"path": drive, "max_file_mb": 1}))


def test_incremental_read(drive: Path) -> None:
    old = datetime(2025, 1, 1, tzinfo=UTC).timestamp()
    for path in drive.rglob("*"):
        if path.is_file():
            os.utime(path, (old, old))
    (drive / "new.md").write_text("# New", encoding="utf-8")
    since = datetime(2026, 1, 1, tzinfo=UTC)
    assert ids(FolderConnector({"path": drive}), since=since) == ["new.md"]


def test_discover_and_streams(drive: Path) -> None:
    connector = FolderConnector({"path": drive})
    streams = {s.name: s.document_count for s in connector.discover()}
    assert streams == {".": 3, "Finance": 1, "Projects": 1}
    assert ids(connector, stream="Finance") == ["Finance/budget.md"]


def test_stream_cannot_escape_the_folder(drive: Path) -> None:
    with pytest.raises(ValueError, match="outside"):
        list(FolderConnector({"path": drive / "Finance"}).read(stream=".."))


def test_check(drive: Path, tmp_path: Path) -> None:
    assert FolderConnector({"path": drive}).check().message.startswith("3 supported files")
    assert not FolderConnector({"path": tmp_path / "missing"}).check().ok
    assert not FolderConnector({"path": drive / "readme.md"}).check().ok


def test_created_by_name_and_parsed(drive: Path) -> None:
    connector = create_connector("folder", {"path": str(drive)}, "shared")
    doc = next(d for d in connector.read() if d.id == "Finance/budget.md")
    parsed = parse_document(doc)
    assert parsed.sections[0].heading == "Budget"
    assert "Travel: 5000" in parsed.sections[0].text
