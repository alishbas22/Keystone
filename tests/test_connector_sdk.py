"""KAN-18: Connector SDK (base class, Document model, manifest, registry)."""

from collections.abc import Iterator
from datetime import UTC, datetime
from importlib.metadata import EntryPoint

import pytest
from pydantic import BaseModel, ValidationError

from keystone.connectors import (
    BaseConnector,
    CheckResult,
    ConnectorError,
    ConnectorManifest,
    Document,
    Stream,
    available_connectors,
    connector_problems,
    create_connector,
    get_connector,
    registry,
)

# --- A minimal connector, as an outside developer would write it ------------------------------


class NotesConfig(BaseModel):
    notes: list[str]


class NotesConnector(BaseConnector[NotesConfig]):
    manifest = ConnectorManifest(name="notes", version="0.1.0", description="In-memory notes.")
    config_model = NotesConfig

    def check(self) -> CheckResult:
        if not self.config.notes:
            return CheckResult.failure("no notes configured")
        return CheckResult.success(f"{len(self.config.notes)} notes")

    def discover(self) -> list[Stream]:
        return [Stream(name="notes", document_count=len(self.config.notes))]

    def read(self, stream: str | None = None, since: datetime | None = None) -> Iterator[Document]:
        for i, note in enumerate(self.config.notes):
            yield Document(id=str(i), source=self.source_name, title=f"Note {i}", text=note)


class NotAConnector:
    pass


class WrongNameConnector(NotesConnector):
    manifest = ConnectorManifest(name="other", version="0.1.0", description="x")


@pytest.fixture
def plugins(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pretend these packages are installed."""
    module = __name__
    entries = [
        EntryPoint("notes", f"{module}:NotesConnector", registry.ENTRY_POINT_GROUP),
        EntryPoint("bogus", f"{module}:NotAConnector", registry.ENTRY_POINT_GROUP),
        EntryPoint("mismatch", f"{module}:WrongNameConnector", registry.ENTRY_POINT_GROUP),
        EntryPoint("missing", "no_such_module:Thing", registry.ENTRY_POINT_GROUP),
    ]
    monkeypatch.setattr(registry, "_entry_points", lambda: entries)


# --- Document model ---------------------------------------------------------------------------


def test_document_needs_exactly_one_body() -> None:
    with pytest.raises(ValidationError, match="exactly one"):
        Document(id="1", source="s:x", title="t")
    with pytest.raises(ValidationError, match="exactly one"):
        Document(id="1", source="s:x", title="t", text="a", content=b"a", file_name="a.txt")


def test_file_documents_need_a_file_name() -> None:
    with pytest.raises(ValidationError, match="file_name"):
        Document(id="1", source="s:x", title="t", content=b"%PDF")


def test_content_hash_ignores_metadata() -> None:
    a = Document(id="1", source="s:x", title="A", text="same", author="ali")
    b = Document(id="2", source="s:y", title="B", text="same")
    c = Document(id="3", source="s:x", title="A", text="different")
    assert a.content_hash == b.content_hash != c.content_hash


# --- Writing a connector ----------------------------------------------------------------------


def test_connector_lifecycle() -> None:
    connector = NotesConnector({"notes": ["Ali works for Acme.", "Acme buys desks."]}, "team")
    assert connector.check().ok
    assert connector.discover() == [Stream(name="notes", document_count=2)]
    docs = list(connector.read())
    assert [d.text for d in docs] == ["Ali works for Acme.", "Acme buys desks."]
    assert docs[0].source == "notes:team"


def test_config_is_validated() -> None:
    with pytest.raises(ValidationError):
        NotesConnector({"notes": "not a list"})


def test_check_reports_problems_instead_of_raising() -> None:
    result = NotesConnector({"notes": []}).check()
    assert not result.ok and "no notes" in result.message


def test_config_schema_for_ui_forms() -> None:
    schema = NotesConnector.config_schema()
    assert schema["properties"]["notes"]["type"] == "array"


def test_connector_must_implement_all_methods() -> None:
    class Incomplete(BaseConnector[NotesConfig]):
        manifest = ConnectorManifest(name="incomplete", version="0", description="x")
        config_model = NotesConfig

        def check(self) -> CheckResult:
            return CheckResult.success()

    with pytest.raises(TypeError, match="abstract"):
        Incomplete({"notes": []})  # type: ignore[abstract]


def test_manifest_names_are_lowercase() -> None:
    with pytest.raises(ValidationError, match="lowercase"):
        ConnectorManifest(name="My CRM", version="1", description="x")


# --- Registry (plugins via entry points) ------------------------------------------------------


@pytest.mark.usefixtures("plugins")
def test_registry_finds_valid_plugins_and_skips_broken_ones() -> None:
    assert list(available_connectors()) == ["notes"]
    problems = connector_problems()
    assert set(problems) == {"bogus", "mismatch", "missing"}
    assert "not a BaseConnector" in problems["bogus"]
    assert "does not match manifest name" in problems["mismatch"]
    assert "failed to load" in problems["missing"]


@pytest.mark.usefixtures("plugins")
def test_create_connector_by_name() -> None:
    connector = create_connector("notes", {"notes": ["hello"]}, "demo")
    assert [d.text for d in connector.read()] == ["hello"]
    assert get_connector("notes") is NotesConnector


@pytest.mark.usefixtures("plugins")
def test_unknown_connector() -> None:
    with pytest.raises(ConnectorError, match="no connector named 'salesforce'"):
        get_connector("salesforce")


def test_documents_can_carry_dates_and_functions() -> None:
    doc = Document(
        id="r1",
        source="postgres:sales",
        title="Order PO-1042",
        text="Order PO-1042 placed by Harbor Hotels Group",
        modified_at=datetime(2025, 8, 12, tzinfo=UTC),
        business_function="Sales",
        metadata={"table": "orders"},
    )
    assert doc.business_function == "Sales"
