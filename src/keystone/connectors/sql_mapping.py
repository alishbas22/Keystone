"""Map relational tables onto the ontology (KAN-20), following NVIDIA auto-ontology's approach.

1. Introspect the schema: tables, columns, primary keys, foreign keys (done by the connector).
2. Propose a mapping automatically:
   - a table becomes an entity type (customers -> Organization) or, if it only links two other
     tables (order_items), a relation between them (Order CONTAINS_PRODUCT Product);
   - columns become ontology properties where the names match (email, sku, order_date);
   - foreign keys become named relations, chosen from the ontology by endpoint types and
     column-name clues (account_manager_id -> manages_account);
   - "customer" and "supplier" tables add a relation to the owning organisation, because being
     a customer is a relationship, not a type.
3. A person reviews the YAML, fixes anything wrong and sets `reviewed: true` (certify step).

`validate_mapping` checks any mapping (proposed or hand-edited) against the ontology and schema.
"""

import re
from collections.abc import Mapping
from pathlib import Path
from typing import Literal, Self

import yaml
from pydantic import BaseModel, Field

from keystone.ontology.models import Ontology

# --- Schema description (filled by introspection) -------------------------------------------


class ColumnInfo(BaseModel):
    name: str
    type: str
    nullable: bool = True


class ForeignKeyInfo(BaseModel):
    column: str
    ref_table: str
    ref_column: str


class TableInfo(BaseModel):
    name: str
    columns: list[ColumnInfo]
    primary_key: list[str] = []
    foreign_keys: list[ForeignKeyInfo] = []
    row_count: int | None = None

    def column_names(self) -> list[str]:
        return [c.name for c in self.columns]

    def fk(self, column: str) -> ForeignKeyInfo | None:
        return next((f for f in self.foreign_keys if f.column == column), None)


# --- Mapping (saved as YAML, reviewed by a person) ---------------------------------------------


class ForeignKeyMapping(BaseModel):
    relation: str
    reverse: bool = Field(
        default=False, description="True: the referenced row is the relation's source."
    )


class RowRelation(BaseModel):
    """A relation every row has to a fixed entity, e.g. customer_of our own company."""

    relation: str
    target: str
    target_type: str = "Organization"


class JunctionMapping(BaseModel):
    """A table that links two others becomes a relation between them."""

    source_column: str
    target_column: str
    relation: str
    properties: dict[str, str] = Field(default={}, description="column -> relation property")


class TableMapping(BaseModel):
    kind: Literal["entity", "junction", "skip"] = "entity"
    entity_type: str | None = None
    name_column: str | None = None
    properties: dict[str, str] = Field(default={}, description="column -> entity property")
    foreign_keys: dict[str, ForeignKeyMapping] = {}
    row_relations: list[RowRelation] = []
    junction: JunctionMapping | None = None
    business_function: str | None = None
    note: str = ""


class SqlMapping(BaseModel):
    reviewed: bool = Field(default=False, description="Set to true after a person checked it.")
    owner_organization: str | None = None
    tables: dict[str, TableMapping] = {}

    def to_yaml(self) -> str:
        header = (
            "# Keystone SQL mapping. Proposed automatically: please review every table,\n"
            "# fix anything wrong, then set `reviewed: true`.\n"
        )
        data = {"reviewed": self.reviewed, **self.model_dump(exclude_defaults=True)}
        return header + yaml.safe_dump(data, sort_keys=False)

    def save(self, path: Path) -> None:
        path.write_text(self.to_yaml(), encoding="utf-8")

    @classmethod
    def load(cls, path: Path) -> Self:
        return cls.model_validate(yaml.safe_load(path.read_text(encoding="utf-8")) or {})


# --- Proposal heuristics -----------------------------------------------------------------------

# table word -> (entity type, relation to the owner organisation, business function)
TABLE_HINTS: dict[str, tuple[str, str | None, str | None]] = {
    "customer": ("Organization", "customer_of", "Sales"),
    "client": ("Organization", "customer_of", "Sales"),
    "account": ("Organization", "customer_of", "Sales"),
    "supplier": ("Organization", "supplier_of", "Operations"),
    "vendor": ("Organization", "supplier_of", "Operations"),
    "company": ("Organization", None, None),
    "organization": ("Organization", None, None),
    "organisation": ("Organization", None, None),
    "employee": ("Person", None, "HR"),
    "staff": ("Person", None, "HR"),
    "person": ("Person", None, None),
    "people": ("Person", None, None),
    "contact": ("Person", None, "Sales"),
    "user": ("Person", None, None),
    "department": ("OrganizationalUnit", None, "HR"),
    "team": ("OrganizationalUnit", None, "HR"),
    "unit": ("OrganizationalUnit", None, "HR"),
    "product": ("Product", None, "Operations"),
    "item": ("Product", None, "Operations"),
    "sku": ("Product", None, "Operations"),
    "order": ("Order", None, "Sales"),
    "invoice": ("Invoice", None, "Finance"),
    "bill": ("Invoice", None, "Finance"),
    "location": ("Place", None, "Operations"),
    "site": ("Place", None, "Operations"),
    "office": ("Place", None, "Operations"),
    "warehouse": ("Place", None, "Operations"),
    "event": ("Event", None, None),
    "meeting": ("Event", None, None),
    "document": ("Document", None, None),
    "store": ("Store", None, "Operations"),
    "shop": ("Store", None, "Operations"),
    "shipment": ("Shipment", None, "Operations"),
    "delivery": ("Shipment", None, "Operations"),
    "segment": ("CustomerSegment", None, "Sales"),
    "product_line": ("ProductLine", None, "Operations"),
    "product_group": ("ProductLine", None, "Operations"),
}

# foreign key column word -> preferred relations, best first
FK_HINTS: dict[str, list[str]] = {
    "account_manager": ["manages_account"],
    "manager": ["reports_to", "manages_account"],
    "boss": ["reports_to"],
    "supervisor": ["reports_to"],
    "customer": ["placed_by", "billed_to", "customer_of"],
    "client": ["placed_by", "billed_to", "customer_of"],
    "buyer": ["placed_by"],
    "seller": ["sold_by"],
    "supplier": ["supplier_of", "supplies"],
    "vendor": ["supplier_of", "supplies"],
    "parent": ["subsidiary_of", "unit_of"],
    "department": ["member_of", "unit_of"],
    "team": ["member_of", "unit_of"],
    "employer": ["works_for"],
    "company": ["works_for"],
    "order": ["invoice_for", "shipment_for"],
    "product": ["contains_product", "ships_product"],
    "store": ["stocked_at"],
    "location": ["located_in"],
    "site": ["located_in"],
    "office": ["located_in"],
    "carrier": ["carried_by"],
    "line": ["variant_of"],
    "segment": ["in_segment"],
    "author": ["written_by"],
    "head": ["head_of"],
    "issuer": ["issued_by"],
}

NAME_COLUMNS = ["name", "full_name", "display_name", "company_name", "legal_name", "title"]

COLUMN_SYNONYMS = {
    "email_address": "email",
    "phone_number": "phone",
    "telephone": "phone",
    "vat_number": "tax_id",
    "company_number": "tax_id",
    "website_url": "website",
    "url": "website",
    "order_no": "order_number",
    "ordered_at": "order_date",
    "placed_at": "order_date",
    "invoice_no": "invoice_number",
    "issued_at": "issue_date",
    "due_date": "payment_due_date",
    "total": "total_amount",
    "amount": "total_amount",
    "qty": "quantity",
}


def _singular(word: str) -> str:
    if word.endswith("ies") and len(word) > 4:
        return word[:-3] + "y"
    if word.endswith(("sses", "xes", "ches", "shes")):
        return word[:-2]
    if word.endswith("s") and not word.endswith("ss") and len(word) > 3:
        return word[:-1]
    return word


def _words(name: str) -> list[str]:
    return [w for w in re.split(r"[^a-z0-9]+", name.lower()) if w]


def _table_hint(table: str, ontology: Ontology) -> tuple[str, str | None, str | None] | None:
    words = [_singular(w) for w in _words(table)]
    for key in ("_".join(words), *(reversed(words))):
        hint = TABLE_HINTS.get(key)
        if hint and hint[0] in ontology.entity_types:
            return hint
    return None


def _fk_stem(column: str) -> str:
    return re.sub(r"_?(id|uuid|key|code|no|ref)$", "", column.lower()).strip("_") or column.lower()


def _relations_between(ontology: Ontology, source: str, target: str) -> list[str]:
    return [
        name
        for name, rel in ontology.relation_types.items()
        if source in ontology.concrete_types(rel.source)
        and target in ontology.concrete_types(rel.target)
    ]


def _choose_fk_relation(
    ontology: Ontology, column: str, own_type: str, ref_type: str
) -> tuple[ForeignKeyMapping | None, str]:
    forward = _relations_between(ontology, own_type, ref_type)
    backward = _relations_between(ontology, ref_type, own_type)
    stem = _fk_stem(column)
    clues = [stem, *reversed(stem.split("_"))]
    for clue in clues:
        for relation in FK_HINTS.get(clue, []):
            if relation in forward:
                return ForeignKeyMapping(relation=relation), ""
            if relation in backward:
                return ForeignKeyMapping(relation=relation, reverse=True), ""
    candidates = [(r, False) for r in forward] + [(r, True) for r in backward]
    if len(candidates) == 1:
        relation, reverse = candidates[0]
        return ForeignKeyMapping(relation=relation, reverse=reverse), ""
    options = ", ".join(sorted({r for r, _ in candidates})) or "none"
    return None, f"{column}: pick a relation ({options})"


def _name_column(table: TableInfo, entity_type: str, ontology: Ontology) -> str | None:
    columns = table.column_names()
    for candidate in NAME_COLUMNS:
        if candidate in columns:
            return candidate
    for column in columns:
        if column.endswith("_name"):
            return column
    for prop in ontology.entity_types[entity_type].identity_properties:
        for column in columns:
            if column == prop or COLUMN_SYNONYMS.get(column) == prop:
                return column
    return None


def _properties(
    columns: list[str], available: Mapping[str, object], skip: set[str]
) -> dict[str, str]:
    mapped: dict[str, str] = {}
    for column in columns:
        if column in skip:
            continue
        prop = column if column in available else COLUMN_SYNONYMS.get(column)
        if prop is not None and prop in available and prop not in mapped.values():
            mapped[column] = prop
    return mapped


def _is_junction(table: TableInfo) -> bool:
    if len(table.foreign_keys) < 2:
        return False
    fk_columns = {f.column for f in table.foreign_keys}
    others = [c for c in table.column_names() if c not in fk_columns and c not in table.primary_key]
    return len(others) <= 3 and not any(c in NAME_COLUMNS or c.endswith("_name") for c in others)


def propose_mapping(
    tables: list[TableInfo], ontology: Ontology, owner_organization: str | None = None
) -> SqlMapping:
    entity_of: dict[str, str] = {}
    mapping = SqlMapping(owner_organization=owner_organization)

    # Pass 1: entity tables.
    for table in tables:
        hint = None if _is_junction(table) else _table_hint(table.name, ontology)
        if hint:
            entity_of[table.name] = hint[0]

    # Pass 2: details for each table.
    for table in tables:
        if table.name in entity_of:
            mapping.tables[table.name] = _entity_mapping(table, entity_of, ontology, mapping)
        elif _is_junction(table):
            mapping.tables[table.name] = _junction_mapping(table, entity_of, ontology)
        else:
            mapping.tables[table.name] = TableMapping(
                kind="skip", note="No matching ontology type; map it by hand or leave skipped."
            )
    return mapping


def _entity_mapping(
    table: TableInfo, entity_of: dict[str, str], ontology: Ontology, mapping: SqlMapping
) -> TableMapping:
    entity_type = entity_of[table.name]
    hint = _table_hint(table.name, ontology)
    fk_columns = {f.column for f in table.foreign_keys}
    name_column = _name_column(table, entity_type, ontology)
    skip = {*fk_columns, *table.primary_key}
    properties = _properties(
        table.column_names(), dict(ontology.entity_types[entity_type].properties), skip
    )
    notes: list[str] = []
    if name_column is None:
        notes.append("no name column found; rows are named '<table> <id>'")

    foreign_keys: dict[str, ForeignKeyMapping] = {}
    for fk in table.foreign_keys:
        ref_type = entity_of.get(fk.ref_table)
        if ref_type is None:
            notes.append(f"{fk.column}: referenced table '{fk.ref_table}' is not mapped")
            continue
        choice, note = _choose_fk_relation(ontology, fk.column, entity_type, ref_type)
        if choice:
            foreign_keys[fk.column] = choice
        else:
            notes.append(note)

    row_relations: list[RowRelation] = []
    if hint and hint[1] and hint[1] in ontology.relation_types:
        if mapping.owner_organization:
            row_relations.append(RowRelation(relation=hint[1], target=mapping.owner_organization))
        else:
            notes.append(f"set owner_organization to add '{hint[1]}' links to your company")

    return TableMapping(
        kind="entity",
        entity_type=entity_type,
        name_column=name_column,
        properties=properties,
        foreign_keys=foreign_keys,
        row_relations=row_relations,
        business_function=hint[2] if hint else None,
        note="; ".join(notes),
    )


def _junction_mapping(
    table: TableInfo, entity_of: dict[str, str], ontology: Ontology
) -> TableMapping:
    mapped_fks = [f for f in table.foreign_keys if f.ref_table in entity_of]
    if len(mapped_fks) < 2:
        return TableMapping(kind="skip", note="links tables that are not mapped")
    first, second = mapped_fks[0], mapped_fks[1]
    for source, target in ((first, second), (second, first)):
        relations = _relations_between(
            ontology, entity_of[source.ref_table], entity_of[target.ref_table]
        )
        if relations:
            relation = relations[0]
            available = dict(ontology.relation_types[relation].properties)
            skip = {*table.primary_key, first.column, second.column}
            source_hint = _table_hint(source.ref_table, ontology)
            return TableMapping(
                kind="junction",
                junction=JunctionMapping(
                    source_column=source.column,
                    target_column=target.column,
                    relation=relation,
                    properties=_properties(table.column_names(), available, skip),
                ),
                business_function=source_hint[2] if source_hint else None,
                note="" if len(relations) == 1 else f"other options: {', '.join(relations[1:])}",
            )
    return TableMapping(kind="skip", note="no ontology relation links these tables")


# --- Validation --------------------------------------------------------------------------------


def validate_mapping(mapping: SqlMapping, ontology: Ontology, tables: list[TableInfo]) -> list[str]:
    """Problems that would produce wrong facts. Empty list = mapping is consistent."""
    problems: list[str] = []
    by_name = {t.name: t for t in tables}
    entity_of = {
        name: tm.entity_type
        for name, tm in mapping.tables.items()
        if tm.kind == "entity" and tm.entity_type
    }
    for name, tm in mapping.tables.items():
        table = by_name.get(name)
        if table is None:
            problems.append(f"{name}: table not found in the database")
            continue
        columns = set(table.column_names())
        if tm.kind == "skip":
            continue
        if tm.kind == "entity":
            problems += _validate_entity(name, tm, table, columns, entity_of, ontology)
        else:
            problems += _validate_junction(name, tm, table, columns, entity_of, ontology)
    return problems


def _validate_entity(
    name: str,
    tm: TableMapping,
    table: TableInfo,
    columns: set[str],
    entity_of: Mapping[str, str | None],
    ontology: Ontology,
) -> list[str]:
    problems: list[str] = []
    entity = ontology.entity_types.get(tm.entity_type or "")
    if entity is None:
        return [f"{name}: unknown entity type '{tm.entity_type}'"]
    if tm.name_column and tm.name_column not in columns:
        problems.append(f"{name}: name column '{tm.name_column}' not found")
    for column, prop in tm.properties.items():
        if column not in columns:
            problems.append(f"{name}.{column}: column not found")
        if prop not in entity.properties:
            problems.append(f"{name}.{column}: '{tm.entity_type}' has no property '{prop}'")
    for column, fk_map in tm.foreign_keys.items():
        fk = table.fk(column)
        if fk is None:
            problems.append(f"{name}.{column}: not a foreign key")
            continue
        ref_type = entity_of.get(fk.ref_table)
        if ref_type is None:
            problems.append(f"{name}.{column}: references unmapped table '{fk.ref_table}'")
            continue
        source, target = (
            (ref_type, tm.entity_type) if fk_map.reverse else (tm.entity_type, ref_type)
        )
        problems += _check_relation(f"{name}.{column}", fk_map.relation, source, target, ontology)
    for row_rel in tm.row_relations:
        problems += _check_relation(
            f"{name} row relation", row_rel.relation, tm.entity_type, row_rel.target_type, ontology
        )
    return problems


def _validate_junction(
    name: str,
    tm: TableMapping,
    table: TableInfo,
    columns: set[str],
    entity_of: Mapping[str, str | None],
    ontology: Ontology,
) -> list[str]:
    junction = tm.junction
    if junction is None:
        return [f"{name}: junction tables need a `junction` section"]
    problems: list[str] = []
    types: list[str | None] = []
    for column in (junction.source_column, junction.target_column):
        fk = table.fk(column)
        if fk is None:
            problems.append(f"{name}.{column}: not a foreign key")
            types.append(None)
        else:
            types.append(entity_of.get(fk.ref_table))
    if all(types):
        problems += _check_relation(name, junction.relation, types[0], types[1], ontology)
    relation = ontology.relation_types.get(junction.relation)
    for column, prop in junction.properties.items():
        if column not in columns:
            problems.append(f"{name}.{column}: column not found")
        elif relation and prop not in relation.properties:
            problems.append(f"{name}.{column}: relation '{junction.relation}' has no '{prop}'")
    return problems


def _check_relation(
    where: str, relation: str, source: str | None, target: str | None, ontology: Ontology
) -> list[str]:
    rel = ontology.relation_types.get(relation)
    if rel is None:
        return [f"{where}: unknown relation '{relation}'"]
    if source not in ontology.concrete_types(rel.source) or target not in ontology.concrete_types(
        rel.target
    ):
        return [f"{where}: '{relation}' cannot link {source} -> {target}"]
    return []
