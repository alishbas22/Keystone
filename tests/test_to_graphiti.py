"""KAN-15: ontology -> Graphiti custom entity and edge types."""

import pytest
from graphiti_core.utils.ontology_utils.entity_types_utils import (
    validate_entity_types,
    validate_identity_properties,
)

from keystone.ontology.loader import core_ontology
from keystone.ontology.to_graphiti import GraphitiSchema, to_graphiti


@pytest.fixture(scope="module")
def schema() -> GraphitiSchema:
    return to_graphiti(core_ontology())


def test_graphiti_accepts_the_generated_types(schema: GraphitiSchema) -> None:
    """Uses Graphiti's own validators, so a Graphiti upgrade that breaks us fails here."""
    assert validate_entity_types(schema.entity_types)
    assert validate_identity_properties(schema.identity_properties, schema.entity_types)


def test_one_model_per_type_and_relation(schema: GraphitiSchema) -> None:
    core = core_ontology()
    assert set(schema.entity_types) == set(core.entity_types)
    assert set(schema.edge_types) == {name.upper() for name in core.relation_types}


def test_descriptions_become_docstrings(schema: GraphitiSchema) -> None:
    core = core_ontology()
    assert schema.entity_types["Person"].__doc__ == core.entity_types["Person"].description
    assert schema.edge_types["REPORTS_TO"].__doc__ == core.relation_types["reports_to"].description


def test_fields_are_optional_with_descriptions(schema: GraphitiSchema) -> None:
    order = schema.entity_types["Order"]
    empty = order.model_validate({})  # a document rarely mentions every detail
    assert empty.model_dump() == {
        "order_number": None, "order_date": None, "total_amount": None, "currency": None,
    }  # fmt: skip
    date_field = order.model_fields["order_date"]
    assert date_field.description is not None and "YYYY-MM-DD" in date_field.description


def test_field_types(schema: GraphitiSchema) -> None:
    order = schema.entity_types["Order"].model_validate(
        {"order_number": "PO-1042", "order_date": "2026-03-01", "total_amount": "1250.50"}
    )
    assert order.model_dump()["total_amount"] == 1250.5  # number -> float


def test_relation_properties(schema: GraphitiSchema) -> None:
    assert set(schema.edge_types["MEMBER_OF"].model_fields) == {"role"}
    assert set(schema.edge_types["CONTAINS_PRODUCT"].model_fields) == {"quantity"}
    assert schema.edge_types["WORKS_FOR"].model_fields == {}


def test_edge_type_map_expands_interfaces(schema: GraphitiSchema) -> None:
    # customer_of: Party -> Organization
    assert "CUSTOMER_OF" in schema.edge_type_map[("Person", "Organization")]
    assert "CUSTOMER_OF" in schema.edge_type_map[("Organization", "Organization")]
    # placed_by: Order -> Party
    assert schema.edge_type_map[("Order", "Person")] == ["PLACED_BY"]
    assert "Party" not in {t for pair in schema.edge_type_map for t in pair}


def test_edge_type_map_only_allows_declared_pairs(schema: GraphitiSchema) -> None:
    assert "REPORTS_TO" not in schema.edge_type_map.get(("Person", "Organization"), [])
    assert ("Product", "Person") not in schema.edge_type_map


def test_identity_properties(schema: GraphitiSchema) -> None:
    assert schema.identity_properties["Product"] == ["sku", "gtin"]
    assert schema.identity_properties["Order"] == ["order_number"]
    assert "Place" not in schema.identity_properties  # no identity fields


def test_add_episode_kwargs(schema: GraphitiSchema) -> None:
    assert set(schema.add_episode_kwargs()) == {
        "entity_types", "edge_types", "edge_type_map", "identity_properties",
    }  # fmt: skip
