"""KAN-17: retail industry pack."""

import pytest
from graphiti_core.utils.ontology_utils.entity_types_utils import (
    validate_entity_types,
    validate_identity_properties,
)

from keystone.ontology import available_packs, core_ontology, load_ontology, to_graphiti
from keystone.ontology.loader import OntologyMergeError
from keystone.ontology.models import Ontology, Triple
from keystone.ontology.standards import check_mappings, load_standards


@pytest.fixture(scope="module")
def retail() -> Ontology:
    return load_ontology(packs=["retail"])


def test_pack_is_discovered_and_recorded(retail: Ontology) -> None:
    assert "retail" in available_packs()
    assert [p.name for p in retail.packs] == ["retail"]
    assert core_ontology().packs == []


def test_adds_retail_concepts(retail: Ontology) -> None:
    assert {"Store", "ProductLine", "Shipment", "CustomerSegment"} <= retail.entity_types.keys()
    assert {"supplier_of", "supplies", "stocked_at", "shipment_for", "in_segment"} <= (
        retail.relation_types.keys()
    )


def test_supplier_and_customer_are_relations_not_types(retail: Ontology) -> None:
    assert "Supplier" not in retail.entity_types
    assert "Customer" not in retail.entity_types
    assert retail.concrete_types(retail.relation_types["supplier_of"].source) == [
        "Person",
        "Organization",
    ]


def test_sku_is_a_product_identity(retail: Ontology) -> None:
    assert "sku" in retail.entity_types["Product"].identity_properties
    assert retail.relation_types["variant_of"].target == ["ProductLine"]


def test_core_is_unchanged_by_the_pack(retail: Ontology) -> None:
    core = core_ontology()
    for name, entity in core.entity_types.items():
        assert retail.entity_types[name] == entity
    for name, relation in core.relation_types.items():
        assert retail.relation_types[name] == relation


def test_store_joins_core_relation_through_interface(retail: Ontology) -> None:
    """located_in is defined in the core; Store takes part only via `implements: [Locatable]`."""
    assert "Store" in retail.concrete_types(retail.relation_types["located_in"].source)


def test_every_mapping_exists_in_the_standards(retail: Ontology) -> None:
    assert check_mappings(retail, load_standards(retail)) == []


def test_graphiti_accepts_retail_types(retail: Ontology) -> None:
    schema = to_graphiti(retail)
    assert validate_entity_types(schema.entity_types)
    assert validate_identity_properties(schema.identity_properties, schema.entity_types)
    assert "LOCATED_IN" in schema.edge_type_map[("Store", "Place")]
    assert schema.edge_type_map[("Shipment", "Order")] == ["SHIPMENT_FOR"]


def test_retail_rules_detect_contradictions(retail: Ontology) -> None:
    rules = retail.rules
    two_carriers = (
        Triple("shp-1", "carried_by", "dhl"),
        Triple("shp-1", "carried_by", "ups"),
    )
    assert rules.contradicts(*two_carriers) is not None
    # Core rules still apply when a pack is loaded.
    assert rules.contradicts(Triple("me", "likes", "fatima"), Triple("me", "dislikes", "fatima"))


def test_unknown_pack_gives_a_clear_error() -> None:
    with pytest.raises(OntologyMergeError, match="Unknown industry pack 'banking'"):
        load_ontology(packs=["banking"])
