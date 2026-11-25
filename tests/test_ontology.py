import copy
from typing import Any

import pytest
from pydantic import ValidationError
from rdflib import Graph

from keystone.ontology.loader import core_ontology, load_raw
from keystone.ontology.models import Ontology
from keystone.ontology.standards import check_mappings, load_standards


@pytest.fixture(scope="module")
def core() -> Ontology:
    return core_ontology()


@pytest.fixture(scope="module")
def standards(core: Ontology) -> Graph:
    return load_standards(core)


@pytest.fixture
def raw() -> dict[str, Any]:
    """The merged core ontology as plain data, for building broken variants."""
    return copy.deepcopy(load_raw())


# --- The core ontology itself -----------------------------------------------------------------


def test_core_has_expected_concepts(core: Ontology) -> None:
    expected = {"Person", "Organization", "OrganizationalUnit", "Product", "Order", "Invoice",
                "Place", "Event", "Document"}  # fmt: skip
    assert expected <= core.entity_types.keys()
    assert {"works_for", "member_of", "reports_to", "customer_of", "placed_by"} <= (
        core.relation_types.keys()
    )
    assert core.business_functions == ["Sales", "Finance", "HR", "Operations"]


def test_every_mapping_exists_in_the_standards(core: Ontology, standards: Graph) -> None:
    assert check_mappings(core, standards) == []


def test_uses_all_three_standards(core: Ontology) -> None:
    prefixes = {term.split(":")[0] for term in core.all_mapped_terms()}
    assert prefixes == {"schema", "org", "prov"}


def test_every_claim_keeps_provenance(core: Ontology) -> None:
    required = {name for name, field in core.provenance.items() if field.required}
    assert {"source", "author", "observed_at", "business_function", "confidence"} <= required
    assert core.provenance["source"].maps_to == "prov:wasDerivedFrom"
    assert core.provenance["observed_at"].maps_to == "prov:generatedAtTime"


def test_identity_properties(core: Ontology) -> None:
    assert core.entity_types["Product"].identity_properties == ["sku", "gtin"]
    assert core.entity_types["Person"].identity_properties == ["email"]


# --- The standards checker catches bad mappings ----------------------------------------------


def test_checker_flags_invented_terms(raw: dict[str, Any], standards: Graph) -> None:
    raw["entity_types"]["Person"]["maps_to"] = "schema:MadeUpThing"
    problems = check_mappings(Ontology.model_validate(raw), standards)
    assert problems == ["schema:MadeUpThing: not defined in the standard"]


def test_checker_flags_property_used_as_class(raw: dict[str, Any], standards: Graph) -> None:
    raw["entity_types"]["Person"]["maps_to"] = "schema:worksFor"
    problems = check_mappings(Ontology.model_validate(raw), standards)
    assert problems == ["schema:worksFor: exists but is not a class"]


# --- The validator rejects inconsistent ontologies --------------------------------------------


def assert_invalid(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Ontology.model_validate(raw)


def test_rejects_relation_to_unknown_type(raw: dict[str, Any]) -> None:
    raw["relation_types"]["works_for"]["target"] = ["Company"]
    assert_invalid(raw, "relation 'works_for' refers to unknown type 'Company'")


def test_rejects_graphiti_reserved_property(raw: dict[str, Any]) -> None:
    raw["entity_types"]["Person"]["properties"]["summary"] = {"type": "text", "description": "x"}
    assert_invalid(raw, "property 'Person.summary' uses a name reserved by Graphiti")


def test_rejects_graphiti_reserved_edge_property(raw: dict[str, Any]) -> None:
    raw["relation_types"]["member_of"]["properties"]["valid_at"] = {
        "type": "date",
        "description": "x",
    }
    assert_invalid(raw, "property 'member_of.valid_at' uses a name reserved by Graphiti")


def test_rejects_undeclared_prefix(raw: dict[str, Any]) -> None:
    raw["entity_types"]["Person"]["maps_to"] = "foaf:Person"
    assert_invalid(raw, "'foaf:Person' uses prefix 'foaf' not declared")


def test_rejects_bad_names(raw: dict[str, Any]) -> None:
    raw["entity_types"]["sales_order"] = raw["entity_types"].pop("Order")
    raw["relation_types"]["WorksFor"] = raw["relation_types"].pop("works_for")
    for rel in raw["relation_types"].values():
        rel["source"] = ["sales_order" if t == "Order" else t for t in rel["source"]]
    assert_invalid(raw, "entity type 'sales_order' must be PascalCase")
    assert_invalid(raw, "relation 'WorksFor' must be snake_case")


def test_rejects_malformed_term(raw: dict[str, Any]) -> None:
    raw["entity_types"]["Person"]["maps_to"] = "https://schema.org/Person"
    assert_invalid(raw, "must look like 'prefix:Term'")


def test_rejects_unknown_fields(raw: dict[str, Any]) -> None:
    raw["entity_types"]["Person"]["colour"] = "blue"  # typos must not be silently ignored
    assert_invalid(raw, "colour")
