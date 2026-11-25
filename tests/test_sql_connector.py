"""KAN-20: SQL connector and NVIDIA auto-ontology style table mapping.

Runs on SQLite always. Set KEYSTONE_TEST_POSTGRES_URL (as CI does) to also run on PostgreSQL:
    postgresql+psycopg://keystone:keystone@localhost:5432/keystone_test
"""

import os
from collections.abc import Iterator
from datetime import date
from pathlib import Path

import pytest
from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Integer,
    MetaData,
    Numeric,
    String,
    Table,
    create_engine,
)

from keystone.connectors import available_connectors, create_connector
from keystone.connectors.sql import SqlConnector, main
from keystone.connectors.sql_mapping import (
    ForeignKeyMapping,
    SqlMapping,
    propose_mapping,
    validate_mapping,
)
from keystone.ontology import load_ontology

ONTOLOGY = load_ontology(packs=["retail"])
OWNER = "Brightline Furnishings Ltd"


def build_sales_db(url: str) -> None:
    md = MetaData()
    Table("departments", md, Column("id", Integer, primary_key=True), Column("name", String(80)))
    Table(
        "employees",
        md,
        Column("id", Integer, primary_key=True),
        Column("full_name", String(80)),
        Column("email", String(120)),
        Column("department_id", ForeignKey("departments.id")),
        Column("manager_id", ForeignKey("employees.id")),
    )
    Table(
        "customers",
        md,
        Column("id", Integer, primary_key=True),
        Column("name", String(120)),
        Column("vat_number", String(40)),
        Column("industry", String(60)),
        Column("account_manager_id", ForeignKey("employees.id")),
    )
    Table(
        "products",
        md,
        Column("id", Integer, primary_key=True),
        Column("name", String(120)),
        Column("sku", String(40)),
    )
    Table(
        "orders",
        md,
        Column("id", Integer, primary_key=True),
        Column("order_number", String(40)),
        Column("order_date", Date),
        Column("total", Numeric(12, 2)),
        Column("customer_id", ForeignKey("customers.id")),
    )
    Table(
        "order_items",
        md,
        Column("id", Integer, primary_key=True),
        Column("order_id", ForeignKey("orders.id")),
        Column("product_id", ForeignKey("products.id")),
        Column("quantity", Integer),
    )
    Table(
        "invoices",
        md,
        Column("id", Integer, primary_key=True),
        Column("invoice_number", String(40)),
        Column("order_id", ForeignKey("orders.id")),
    )
    Table("audit_log", md, Column("id", Integer, primary_key=True), Column("event", String(200)))

    engine = create_engine(url)
    md.drop_all(engine)
    md.create_all(engine)
    t = md.tables
    with engine.begin() as conn:
        conn.execute(t["departments"].insert(), [{"id": 1, "name": "Sales team"}])
        conn.execute(
            t["employees"].insert(),
            [
                {"id": 1, "full_name": "Omar Siddiqui", "email": "omar@bl.example", "department_id": 1, "manager_id": None},
                {"id": 2, "full_name": "Sara Khan", "email": None, "department_id": 1, "manager_id": 1},
                {"id": 3, "full_name": "Bilal Ahmed", "email": None, "department_id": 1, "manager_id": 1},
            ],
        )  # fmt: skip
        conn.execute(
            t["customers"].insert(),
            [
                {"id": 1, "name": "Harbor Hotels Group", "vat_number": "GB123", "industry": "Hospitality", "account_manager_id": 2},
                {"id": 2, "name": "Crestview Bank", "vat_number": None, "industry": "Banking", "account_manager_id": 3},
            ],
        )  # fmt: skip
        conn.execute(
            t["products"].insert(), [{"id": 1, "name": "Ergo Desk Chair", "sku": "BF-CH-100"}]
        )
        conn.execute(
            t["orders"].insert(),
            [{"id": 1, "order_number": "PO-1042", "order_date": date(2025, 8, 12), "total": 120000, "customer_id": 1}],
        )  # fmt: skip
        conn.execute(
            t["order_items"].insert(), [{"id": 1, "order_id": 1, "product_id": 1, "quantity": 200}]
        )
        conn.execute(
            t["invoices"].insert(), [{"id": 1, "invoice_number": "INV-2025-031", "order_id": 1}]
        )
        conn.execute(t["audit_log"].insert(), [{"id": 1, "event": "login"}])
    engine.dispose()


def _urls() -> list[str]:
    urls = ["sqlite"]
    if os.environ.get("KEYSTONE_TEST_POSTGRES_URL"):
        urls.append("postgres")
    return urls


@pytest.fixture(params=_urls())
def connector(request: pytest.FixtureRequest, tmp_path: Path) -> Iterator[SqlConnector]:
    if request.param == "sqlite":
        url = f"sqlite:///{(tmp_path / 'sales.db').as_posix()}"
    else:
        url = os.environ["KEYSTONE_TEST_POSTGRES_URL"]
    build_sales_db(url)
    conn = SqlConnector({"url": url, "owner_organization": OWNER}, "sales", ontology=ONTOLOGY)
    yield conn
    conn.engine.dispose()


# --- Introspection and proposal ----------------------------------------------------------------


def test_registered_as_a_plugin() -> None:
    assert {"folder", "sql"} <= set(available_connectors())


def test_describe_schema(connector: SqlConnector) -> None:
    tables = {t.name: t for t in connector.describe_schema()}
    assert tables["customers"].row_count == 2
    fk = tables["customers"].fk("account_manager_id")
    assert fk is not None and fk.ref_table == "employees"


def test_proposed_mapping(connector: SqlConnector) -> None:
    mapping = propose_mapping(connector.describe_schema(), ONTOLOGY, OWNER)
    t = mapping.tables
    # Tables -> entity types
    assert (t["customers"].entity_type, t["employees"].entity_type) == ("Organization", "Person")
    assert t["departments"].entity_type == "OrganizationalUnit"
    assert t["orders"].entity_type == "Order"
    # Columns -> properties (with synonyms)
    assert t["customers"].properties == {"vat_number": "tax_id"}
    assert t["orders"].properties == {"order_number": "order_number", "order_date": "order_date", "total": "total_amount"}  # fmt: skip
    assert t["employees"].name_column == "full_name"
    # Foreign keys -> relations, using column-name clues and direction
    assert t["customers"].foreign_keys["account_manager_id"] == ForeignKeyMapping(
        relation="manages_account", reverse=True
    )
    assert t["employees"].foreign_keys["manager_id"].relation == "reports_to"
    assert t["employees"].foreign_keys["department_id"].relation == "member_of"
    assert t["orders"].foreign_keys["customer_id"].relation == "placed_by"
    assert t["invoices"].foreign_keys["order_id"].relation == "invoice_for"
    # Being a customer is a relationship to the owner organisation
    assert [r.relation for r in t["customers"].row_relations] == ["customer_of"]
    # Junction table -> relation with properties
    junction = t["order_items"].junction
    assert t["order_items"].kind == "junction" and junction is not None
    assert (junction.relation, junction.properties) == (
        "contains_product",
        {"quantity": "quantity"},
    )
    # Unknown tables are skipped, not guessed
    assert t["audit_log"].kind == "skip"
    # Business functions
    assert (t["customers"].business_function, t["invoices"].business_function) == (
        "Sales",
        "Finance",
    )
    assert validate_mapping(mapping, ONTOLOGY, connector.describe_schema()) == []


def test_validation_catches_hand_editing_mistakes(connector: SqlConnector) -> None:
    tables = connector.describe_schema()
    mapping = propose_mapping(tables, ONTOLOGY, OWNER)
    mapping.tables["customers"].properties["vat_number"] = "vat"
    mapping.tables["customers"].foreign_keys["account_manager_id"] = ForeignKeyMapping(
        relation="reports_to"
    )
    mapping.tables["orders"].name_column = "ordr_number"
    problems = validate_mapping(mapping, ONTOLOGY, tables)
    assert "customers.vat_number: 'Organization' has no property 'vat'" in problems
    assert (
        "customers.account_manager_id: 'reports_to' cannot link Organization -> Person" in problems
    )
    assert "orders: name column 'ordr_number' not found" in problems


# --- Reading -------------------------------------------------------------------------------------


def test_rows_become_explicit_fact_records(connector: SqlConnector) -> None:
    docs = {d.metadata["table"]: d for d in connector.read()}
    customers = docs["customers"].text or ""
    assert "Record customers/1: Harbor Hotels Group (Organization)" in customers
    assert "- tax id: GB123" in customers
    assert "- industry: Hospitality" in customers  # unmapped columns are kept as context
    assert "- Sara Khan (Person) MANAGES_ACCOUNT Harbor Hotels Group (Organization)" in customers
    assert f"- Harbor Hotels Group (Organization) CUSTOMER_OF {OWNER} (Organization)" in customers
    assert "- Sara Khan (Person) REPORTS_TO Omar Siddiqui (Person)" in (
        docs["employees"].text or ""
    )
    assert "- PO-1042 (Order) CONTAINS_PRODUCT Ergo Desk Chair (Product) (quantity: 200)" in (
        docs["order_items"].text or ""
    )
    assert "audit_log" not in docs


def test_document_metadata(connector: SqlConnector) -> None:
    doc = next(d for d in connector.read(stream="invoices"))
    assert doc.source == "sql:sales"
    assert doc.business_function == "Finance"
    assert doc.metadata["mapping_reviewed"] == "false"
    assert "postgres" not in doc.text and "sqlite" not in (doc.text or "")  # no URL leaks


def test_batches(connector: SqlConnector) -> None:
    connector.config = connector.config.model_copy(update={"rows_per_document": 2})
    ids = [d.id for d in connector.read(stream="employees")]
    assert ids == ["employees/1-2", "employees/3-3"]


def test_reviewed_mapping_file_is_used(connector: SqlConnector, tmp_path: Path) -> None:
    mapping = propose_mapping(connector.describe_schema(), ONTOLOGY, OWNER)
    mapping.reviewed = True
    mapping.tables["products"].business_function = "Sales"
    path = tmp_path / "mapping.yaml"
    mapping.save(path)
    assert SqlMapping.load(path) == mapping
    connector.config = connector.config.model_copy(update={"mapping_file": path})
    doc = next(connector.read(stream="products"))
    assert (doc.business_function, doc.metadata["mapping_reviewed"]) == ("Sales", "true")


def test_invalid_mapping_stops_reading(connector: SqlConnector, tmp_path: Path) -> None:
    mapping = propose_mapping(connector.describe_schema(), ONTOLOGY, OWNER)
    mapping.tables["orders"].entity_type = "Purchase"
    path = tmp_path / "bad.yaml"
    mapping.save(path)
    connector.config = connector.config.model_copy(update={"mapping_file": path})
    assert not connector.check().ok
    with pytest.raises(ValueError, match="unknown entity type 'Purchase'"):
        list(connector.read())


def test_check_and_discover(connector: SqlConnector) -> None:
    assert connector.check().ok
    streams = {s.name: s for s in connector.discover()}
    assert streams["customers"].description == "-> Organization"
    assert streams["order_items"].description == "-> CONTAINS_PRODUCT"
    assert streams["audit_log"].description == "(skipped)"


def test_check_reports_bad_connection() -> None:
    bad = create_connector("sql", {"url": "sqlite:///Z:/does/not/exist/x.db"})
    assert not bad.check().ok


def test_propose_cli(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    url = f"sqlite:///{(tmp_path / 'cli.db').as_posix()}"
    build_sales_db(url)
    out = tmp_path / "proposed.yaml"
    main(["propose", url, "--owner", OWNER, "--out", str(out)])
    text = out.read_text(encoding="utf-8")
    assert text.startswith("# Keystone SQL mapping")
    assert "\nreviewed: false\n" in text  # the reviewer sees what to flip
    assert "customers" in capsys.readouterr().out
