"""KAN-35: the sample company dataset, its sales database and its ground truth (no LLM)."""

from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
import yaml

from keystone.connectors.folder import FolderConnector
from keystone.connectors.sql import SqlConnector
from keystone.connectors.sql_mapping import SqlMapping, validate_mapping
from keystone.graph.provenance import Provenance
from keystone.graph.truth import Evidence, Fact, detect_conflicts
from keystone.ingest.parse import date_from_filename, parse_document
from keystone.ontology import load_ontology

DEMO = Path(__file__).resolve().parents[1] / "demo"
ONTOLOGY = load_ontology(packs=["retail"])
TRUTH: dict[str, Any] = yaml.safe_load((DEMO / "ground_truth.yaml").read_text(encoding="utf-8"))
FILES = {p.name: p for p in (DEMO / "data").rglob("*.*")}


def test_thirty_documents_in_business_function_folders() -> None:
    assert len(FILES) == 30
    docs = list(FolderConnector({"path": DEMO / "data"}, "samples").read())
    by_function = {d.business_function for d in docs}
    assert by_function == {"Sales", "Finance", "HR", "Operations"}
    formats = {Path(name).suffix for name in FILES}
    assert formats == {".pdf", ".docx", ".xlsx", ".pptx", ".eml", ".md", ".csv", ".txt"}


@pytest.mark.slow
def test_every_document_parses_and_is_dated() -> None:
    for document in FolderConnector({"path": DEMO / "data"}, "samples").read():
        parsed = parse_document(document)
        assert parsed.sections, document.id
        assert date_from_filename(document.file_name or "") is not None, document.id


def test_ground_truth_uses_real_documents_and_ontology_relations() -> None:
    for name, entry in TRUTH["facts"].items():
        assert name in FILES, name
        for subject, relation, obj in entry["facts"]:
            assert relation in ONTOLOGY.relation_types, (name, relation)
            assert subject and obj
    for conflict in TRUTH["contradictions"]:
        for claim in conflict["claims"]:
            assert claim["document"] in FILES, claim


def _fact(i: int, claim: dict[str, Any], default_relation: str) -> Fact:
    """The fact a claim's document should produce, with that document's date and source type."""
    name = claim["document"]
    when = date_from_filename(name) or datetime.now(UTC)
    kind = Provenance(
        doc_id=name, document_id=name, source="folder:samples", title=name, file=name,
        observed_at=when, business_function="Sales",
    ).source_type  # fmt: skip
    return Fact(
        id=str(i), subject=claim["subject"], relation=claim.get("relation", default_relation),
        object=claim["object"], text=name, observed_at=when,
        evidence=(Evidence(doc_id=name, source_type=kind, observed_at=when),),
    )  # fmt: skip


def _merge_same(facts: list[Fact]) -> list[Fact]:
    """Graphiti merges identical facts from several documents into one fact with all sources."""
    merged: dict[tuple[str, str, str], Fact] = {}
    for f in facts:
        key = f.triple
        if key in merged:
            old = merged[key]
            merged[key] = Fact(
                id=old.id, subject=old.subject, relation=old.relation, object=old.object,
                text=old.text, observed_at=max(old.observed_at, f.observed_at),  # type: ignore[type-var]
                evidence=old.evidence + f.evidence,
            )  # fmt: skip
        else:
            merged[key] = f
    return list(merged.values())


@pytest.mark.parametrize("conflict", TRUTH["contradictions"], ids=lambda c: c["id"])
def test_planted_contradictions_resolve_as_designed(conflict: dict[str, Any]) -> None:
    facts = _merge_same(
        [_fact(i, c, conflict.get("relation", "")) for i, c in enumerate(conflict["claims"])]
    )
    [found] = detect_conflicts(facts, ONTOLOGY.rules)
    assert str(found.decided_by) == conflict["rule"]
    winner = next((f for f in facts if f.id == found.winner), None)
    expected = conflict["expected_current"]
    if expected is None:
        assert winner is None
    else:
        assert winner is not None and expected in (winner.subject, winner.object, winner.relation)


# --- The sales database -----------------------------------------------------------------------------


@pytest.fixture
def sales_db(tmp_path: Path) -> str:
    import sqlite3

    path = tmp_path / "sales.db"
    with sqlite3.connect(path) as conn:
        conn.executescript((DEMO / "sales" / "schema.sql").read_text(encoding="utf-8"))
    return f"sqlite:///{path.as_posix()}"


def test_reviewed_mapping_matches_the_database(sales_db: str) -> None:
    mapping = SqlMapping.load(DEMO / "sales" / "mapping.yaml")
    assert mapping.reviewed and mapping.owner_organization == "Brightline Furnishings Ltd"
    connector = SqlConnector(
        {"url": sales_db, "mapping_file": DEMO / "sales" / "mapping.yaml"},
        "sales",
        ontology=ONTOLOGY,
    )
    assert validate_mapping(mapping, ONTOLOGY, connector.describe_schema()) == []
    records = {d.metadata["table"]: d.text or "" for d in connector.read()}
    assert (
        "- Bilal Ahmed (Person) MANAGES_ACCOUNT Harbor Hotels Group (Organization)"
        in records["customers"]
    )
    assert (
        "- Harbor Hotels Group (Organization) CUSTOMER_OF Brightline Furnishings Ltd (Organization)"
        in records["customers"]
    )
    assert (
        "- PO-1042 (Order) CONTAINS_PRODUCT Ergo Desk Chair (Product) (quantity: 200)"
        in records["order_items"]
    )
    docs = {d.metadata["table"]: d for d in connector.read()}
    assert docs["customers"].modified_at == datetime(2026, 9, 15, tzinfo=UTC)  # from updated_at
    assert docs["invoices"].business_function == "Finance"
