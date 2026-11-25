"""SQL database connector (KAN-20): PostgreSQL, and any database SQLAlchemy supports.

Rows are mapped onto the ontology with a reviewed mapping file (see sql_mapping.py) and emitted
as short text records that state each fact explicitly, e.g.

    Record customers/1: Harbor Hotels Group (Organization)
    - tax id: GB123456
    - Harbor Hotels Group (Organization) CUSTOMER_OF Brightline Furnishings Ltd (Organization)
    - Sara Khan (Person) MANAGES_ACCOUNT Harbor Hotels Group (Organization)

Propose a mapping for review:

    uv run python -m keystone.connectors.sql propose "postgresql+psycopg://user:pw@host/db" \
        --owner "Brightline Furnishings Ltd" --out mapping.yaml
"""

import argparse
from collections.abc import Iterator, Sequence
from datetime import date, datetime
from decimal import Decimal
from functools import cached_property
from pathlib import Path
from typing import Any

from pydantic import BaseModel, Field, SecretStr
from sqlalchemy import Engine, MetaData, Table, create_engine, func, inspect, select, text

from keystone.connectors.base import (
    BaseConnector,
    CheckResult,
    ConnectorManifest,
    Document,
    Stream,
)
from keystone.connectors.sql_mapping import (
    ColumnInfo,
    ForeignKeyInfo,
    SqlMapping,
    TableInfo,
    TableMapping,
    propose_mapping,
    validate_mapping,
)
from keystone.ontology import Ontology, active_ontology

UPDATED_COLUMNS = ("updated_at", "modified_at", "last_modified", "last_updated")


class SqlConfig(BaseModel):
    url: SecretStr = Field(
        description="SQLAlchemy URL, e.g. postgresql+psycopg://user:password@host:5432/db"
    )
    schema_name: str | None = Field(default=None, description="Database schema (default: public).")
    tables: list[str] | None = Field(default=None, description="Only these tables (default: all).")
    mapping_file: Path | None = Field(
        default=None, description="Reviewed mapping YAML. Without it, a proposal is used."
    )
    owner_organization: str | None = Field(
        default=None, description="Your company's name, for customer_of / supplier_of links."
    )
    rows_per_document: int = Field(default=25, ge=1, le=1000)
    business_function: str | None = Field(
        default=None, description="Owner when the mapping does not set one per table."
    )


class SqlConnector(BaseConnector[SqlConfig]):
    manifest = ConnectorManifest(
        name="sql",
        version="0.1.0",
        description="PostgreSQL (or any SQLAlchemy database) mapped onto the ontology.",
        supports_incremental=True,
    )
    config_model = SqlConfig

    def __init__(
        self,
        config: SqlConfig | dict[str, Any],
        instance_name: str | None = None,
        ontology: Ontology | None = None,
    ):
        super().__init__(config, instance_name)
        self.ontology = ontology or active_ontology()

    # --- connection and schema -----------------------------------------------------------------

    @cached_property
    def engine(self) -> Engine:
        return create_engine(self.config.url.get_secret_value(), pool_pre_ping=True)

    def describe_schema(self) -> list[TableInfo]:
        inspector = inspect(self.engine)
        schema = self.config.schema_name
        names = self.config.tables or sorted(inspector.get_table_names(schema=schema))
        tables = []
        for name in names:
            columns = [
                ColumnInfo(name=c["name"], type=str(c["type"]), nullable=bool(c.get("nullable")))
                for c in inspector.get_columns(name, schema=schema)
            ]
            pk = inspector.get_pk_constraint(name, schema=schema).get("constrained_columns") or []
            fks = [
                ForeignKeyInfo(
                    column=fk["constrained_columns"][0],
                    ref_table=fk["referred_table"],
                    ref_column=fk["referred_columns"][0],
                )
                for fk in inspector.get_foreign_keys(name, schema=schema)
                if len(fk["constrained_columns"]) == 1  # composite keys: not supported yet
            ]
            tables.append(TableInfo(name=name, columns=columns, primary_key=pk, foreign_keys=fks))
        with self.engine.connect() as conn:
            for info in tables:
                info.row_count = conn.execute(
                    select(func.count()).select_from(self._table(info.name))
                ).scalar_one()
        return tables

    def mapping(self) -> SqlMapping:
        path = self.config.mapping_file
        if path is not None and path.exists():
            return SqlMapping.load(path)
        return propose_mapping(
            self.describe_schema(), self.ontology, self.config.owner_organization
        )

    def _table(self, name: str) -> Table:
        return Table(name, MetaData(), autoload_with=self.engine, schema=self.config.schema_name)

    # --- SDK methods -----------------------------------------------------------------------------

    def check(self) -> CheckResult:
        try:
            with self.engine.connect() as conn:
                conn.execute(text("SELECT 1"))
            tables = self.describe_schema()
        except Exception as exc:
            return CheckResult.failure(f"Cannot connect: {type(exc).__name__}: {exc}")
        problems = validate_mapping(self.mapping(), self.ontology, tables)
        if problems:
            return CheckResult.failure("Mapping problems: " + "; ".join(problems))
        return CheckResult.success(f"Connected. {len(tables)} tables.")

    def discover(self) -> list[Stream]:
        mapping = self.mapping()
        streams = []
        for table in self.describe_schema():
            tm = mapping.tables.get(table.name, TableMapping(kind="skip"))
            what = (
                f"-> {tm.entity_type}"
                if tm.kind == "entity"
                else f"-> {tm.junction.relation.upper()}"
                if tm.kind == "junction" and tm.junction
                else "(skipped)"
            )
            streams.append(
                Stream(name=table.name, description=what, document_count=table.row_count)
            )
        return streams

    def read(self, stream: str | None = None, since: datetime | None = None) -> Iterator[Document]:
        tables = {t.name: t for t in self.describe_schema()}
        mapping = self.mapping()
        problems = validate_mapping(mapping, self.ontology, list(tables.values()))
        if problems:
            raise ValueError("Fix the SQL mapping first: " + "; ".join(problems))
        names = _NameLookup(self, tables, mapping)
        for table_name, tm in mapping.tables.items():
            if tm.kind == "skip" or (stream is not None and stream != table_name):
                continue
            yield from self._read_table(tables[table_name], tm, mapping, names, since)

    # --- rendering -------------------------------------------------------------------------------

    def _read_table(
        self,
        info: TableInfo,
        tm: TableMapping,
        mapping: SqlMapping,
        names: "_NameLookup",
        since: datetime | None,
    ) -> Iterator[Document]:
        table = self._table(info.name)
        query = select(table).order_by(*(table.c[c] for c in info.primary_key))
        updated = next((c for c in UPDATED_COLUMNS if c in info.column_names()), None)
        if since is not None and updated:
            query = query.where(table.c[updated] > since)

        with self.engine.connect() as conn:
            rows = [dict(r._mapping) for r in conn.execute(query)]
        size = self.config.rows_per_document
        for start in range(0, len(rows), size):
            batch = rows[start : start + size]
            keys = [_row_key(info, row) for row in batch]
            heading = f"## Table {info.name}" + (
                f" ({tm.entity_type})" if tm.kind == "entity" else " (links)"
            )
            records = [
                self._render_row(info, tm, mapping, names, row, key)
                for row, key in zip(batch, keys, strict=True)
            ]
            modified = [row[updated] for row in batch if updated and row.get(updated)]
            yield Document(
                id=f"{info.name}/{keys[0]}-{keys[-1]}",
                source=self.source_name,
                title=f"{info.name} {keys[0]}-{keys[-1]}",
                text=heading + "\n\n" + "\n\n".join(records),
                modified_at=max(modified) if modified else None,
                business_function=tm.business_function or self.config.business_function,
                metadata={
                    "table": info.name,
                    "entity_type": tm.entity_type or "",
                    "mapping_reviewed": str(mapping.reviewed).lower(),
                },
            )

    def _render_row(
        self,
        info: TableInfo,
        tm: TableMapping,
        mapping: SqlMapping,
        names: "_NameLookup",
        row: dict[str, Any],
        key: str,
    ) -> str:
        if tm.kind == "junction" and tm.junction:
            return self._render_junction(info, tm, names, row, key)
        subject = names.name_of(info.name, row)
        lines = [f"Record {info.name}/{key}: {subject} ({tm.entity_type})"]
        fk_columns = {f.column for f in info.foreign_keys}
        for column, value in row.items():
            if value is None or column in info.primary_key or column in fk_columns:
                continue
            if column == tm.name_column:
                continue
            label = tm.properties.get(column, column).replace("_", " ")
            lines.append(f"- {label}: {_fmt(value)}")
        for column, fk_map in tm.foreign_keys.items():
            fk = info.fk(column)
            if fk is None or row.get(column) is None:
                continue
            other = names.name_by_key(fk.ref_table, row[column])
            other_type = names.entity_type(fk.ref_table)
            ends = [(subject, tm.entity_type), (other, other_type)]
            if fk_map.reverse:
                ends.reverse()
            lines.append(_relation_line(ends[0], fk_map.relation, ends[1]))
        for row_rel in tm.row_relations:
            lines.append(
                _relation_line(
                    (subject, tm.entity_type),
                    row_rel.relation,
                    (row_rel.target, row_rel.target_type),
                )
            )
        return "\n".join(lines)

    def _render_junction(
        self, info: TableInfo, tm: TableMapping, names: "_NameLookup", row: dict[str, Any], key: str
    ) -> str:
        assert tm.junction is not None
        junction = tm.junction
        ends = []
        for column in (junction.source_column, junction.target_column):
            fk = info.fk(column)
            assert fk is not None  # checked by validate_mapping
            ends.append(
                (names.name_by_key(fk.ref_table, row[column]), names.entity_type(fk.ref_table))
            )
        details = ", ".join(
            f"{prop.replace('_', ' ')}: {_fmt(row[col])}"
            for col, prop in junction.properties.items()
            if row.get(col) is not None
        )
        line = _relation_line(ends[0], junction.relation, ends[1])
        return f"Record {info.name}/{key}:\n{line}" + (f" ({details})" if details else "")


class _NameLookup:
    """Human-readable names for rows, used to resolve foreign keys."""

    def __init__(self, connector: SqlConnector, tables: dict[str, TableInfo], mapping: SqlMapping):
        self.connector = connector
        self.tables = tables
        self.mapping = mapping
        self._cache: dict[str, dict[str, str]] = {}

    def entity_type(self, table: str) -> str | None:
        tm = self.mapping.tables.get(table)
        return tm.entity_type if tm else None

    def name_of(self, table: str, row: dict[str, Any]) -> str:
        tm = self.mapping.tables.get(table)
        if tm and tm.name_column and row.get(tm.name_column) is not None:
            return str(row[tm.name_column])
        return f"{table} {_row_key(self.tables[table], row)}"

    def name_by_key(self, table: str, key: Any) -> str:
        if table not in self._cache:
            info = self.tables[table]
            sql_table = self.connector._table(table)
            with self.connector.engine.connect() as conn:
                rows = [dict(r._mapping) for r in conn.execute(select(sql_table))]
            self._cache[table] = {_row_key(info, row): self.name_of(table, row) for row in rows}
        return self._cache[table].get(str(key), f"{table} {key}")


def _row_key(info: TableInfo, row: dict[str, Any]) -> str:
    columns = info.primary_key or info.column_names()[:1]
    return "-".join(str(row[c]) for c in columns)


def _relation_line(
    source: tuple[str, str | None], relation: str, target: tuple[str, str | None]
) -> str:
    return f"- {source[0]} ({source[1]}) {relation.upper()} {target[0]} ({target[1]})"


def _fmt(value: Any) -> str:
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, Decimal):
        return format(value, "f")
    return str(value)


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Propose an ontology mapping for a SQL database.")
    sub = parser.add_subparsers(dest="command", required=True)
    propose = sub.add_parser("propose", help="write a mapping YAML for review")
    propose.add_argument("url", help="SQLAlchemy URL")
    propose.add_argument("--schema")
    propose.add_argument("--owner", help="your company's name (for customer_of links)")
    propose.add_argument("--out", type=Path, default=Path("sql_mapping.yaml"))
    args = parser.parse_args(argv)

    connector = SqlConnector(
        {"url": args.url, "schema_name": args.schema, "owner_organization": args.owner}
    )
    tables = connector.describe_schema()
    mapping = propose_mapping(tables, connector.ontology, args.owner)
    mapping.save(args.out)
    print(f"Wrote {args.out}. Review it, then set `reviewed: true`.")
    for name, tm in mapping.tables.items():
        target = tm.entity_type or (tm.junction.relation if tm.junction else "skipped")
        print(f"  {name:<20} {tm.kind:<9} {target}" + (f"   NOTE: {tm.note}" if tm.note else ""))
    for problem in validate_mapping(mapping, connector.ontology, tables):
        print(f"  PROBLEM: {problem}")


if __name__ == "__main__":
    main()
