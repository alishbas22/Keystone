"""KAN-16: shared interfaces and relation rules (contradiction detection)."""

import copy
from pathlib import Path
from typing import Any

import pytest
import yaml
from pydantic import ValidationError

from keystone.ontology.loader import OntologyMergeError, core_ontology, load_ontology, load_raw
from keystone.ontology.models import Ontology, RelationRules, Triple


@pytest.fixture(scope="module")
def rules() -> RelationRules:
    return core_ontology().rules


@pytest.fixture
def raw() -> dict[str, Any]:
    return copy.deepcopy(load_raw())


def assert_invalid(raw: dict[str, Any], message: str) -> None:
    with pytest.raises(ValidationError, match=message):
        Ontology.model_validate(raw)


# --- Interfaces -------------------------------------------------------------------------------


def test_party_and_transactable() -> None:
    core = core_ontology()
    assert core.implementers("Party") == ["Person", "Organization"]
    assert core.implementers("Transactable") == ["Order", "Invoice"]


def test_interface_endpoints_expand_to_concrete_types() -> None:
    core = core_ontology()
    assert core.relation_types["customer_of"].source == ["Party"]
    assert core.concrete_types(["Party", "Product"]) == ["Person", "Organization", "Product"]
    assert core.concrete_types(["Person", "Party"]) == ["Person", "Organization"]  # no duplicates


def test_implementer_must_have_interface_properties(raw: dict[str, Any]) -> None:
    del raw["entity_types"]["Order"]["properties"]["currency"]
    assert_invalid(raw, "'Order' implements 'Transactable' so it needs property 'currency'")


def test_implementer_property_types_must_match(raw: dict[str, Any]) -> None:
    raw["entity_types"]["Invoice"]["properties"]["total_amount"]["type"] = "string"
    assert_invalid(raw, "'Invoice' implements 'Transactable' so it needs property 'total_amount'")


def test_rejects_unknown_interface(raw: dict[str, Any]) -> None:
    raw["entity_types"]["Product"]["implements"] = ["Sellable"]
    assert_invalid(raw, "'Product' implements unknown interface 'Sellable'")


def test_interface_name_cannot_clash_with_entity_type(raw: dict[str, Any]) -> None:
    raw["interfaces"]["Person"] = {"description": "x"}
    assert_invalid(raw, "'Person' is both an interface and an entity type")


# --- Contradiction rules ----------------------------------------------------------------------


def test_opposites_contradict(rules: RelationRules) -> None:
    """The Fatima example: 'me likes Fatima' vs 'me dislikes Fatima'."""
    a = Triple("me", "likes", "fatima")
    b = Triple("me", "dislikes", "fatima")
    assert rules.contradicts(a, b) == "'likes' and 'dislikes' are opposites"
    assert rules.contradicts(b, a) is not None  # order does not matter


def test_opposites_about_different_people_do_not_contradict(rules: RelationRules) -> None:
    assert (
        rules.contradicts(Triple("me", "likes", "fatima"), Triple("me", "dislikes", "ali")) is None
    )


def test_same_claim_twice_is_not_a_contradiction(rules: RelationRules) -> None:
    claim = Triple("sara", "reports_to", "ali")
    assert rules.contradicts(claim, claim) is None


def test_single_valued_per_source(rules: RelationRules) -> None:
    """A person has one current manager."""
    reason = rules.contradicts(
        Triple("sara", "reports_to", "ali"), Triple("sara", "reports_to", "omar")
    )
    assert reason is not None and "one current direct manager" in reason


def test_single_valued_per_target(rules: RelationRules) -> None:
    """A customer has one account manager, but a manager can handle many customers."""
    two_managers = (
        Triple("sara", "manages_account", "acme"),
        Triple("bilal", "manages_account", "acme"),
    )
    two_customers = (
        Triple("sara", "manages_account", "acme"),
        Triple("sara", "manages_account", "globex"),
    )
    assert rules.contradicts(*two_managers) is not None
    assert rules.contradicts(*two_customers) is None


def test_multi_valued_relation_never_conflicts(rules: RelationRules) -> None:
    """People can work for two organisations; works_for has no single-value rule."""
    assert (
        rules.contradicts(Triple("ali", "works_for", "acme"), Triple("ali", "works_for", "globex"))
        is None
    )


# --- Rule validation --------------------------------------------------------------------------


def test_rejects_rule_on_unknown_relation(raw: dict[str, Any]) -> None:
    raw["rules"]["opposites"].append(["trusts", "distrusts"])
    assert_invalid(raw, "opposite rule refers to unknown relation 'trusts'")


def test_rejects_self_opposite(raw: dict[str, Any]) -> None:
    raw["rules"]["opposites"] = [["knows", "knows"]]
    assert_invalid(raw, "'knows' cannot be its own opposite")


def test_opposites_must_connect_same_types(raw: dict[str, Any]) -> None:
    raw["rules"]["opposites"] = [["works_for", "knows"]]
    assert_invalid(raw, "opposites 'works_for' and 'knows' must connect the same types")


def test_rejects_duplicate_single_valued_rule(raw: dict[str, Any]) -> None:
    raw["rules"]["single_valued"].append(
        {"relation": "reports_to", "per": "target", "description": "x"}
    )
    assert_invalid(raw, "'reports_to' has more than one single-valued rule")


# --- Extend, don't modify ---------------------------------------------------------------------


def write_pack(tmp_path: Path, content: dict[str, Any]) -> Path:
    path = tmp_path / "pack.yaml"
    path.write_text(yaml.safe_dump(content), encoding="utf-8")
    return path


def test_extension_can_add_types_that_join_interfaces(tmp_path: Path) -> None:
    pack = write_pack(
        tmp_path,
        {
            "entity_types": {
                "Bank": {
                    "description": "A bank.",
                    "maps_to": "schema:BankOrCreditUnion",
                    "implements": ["Party"],
                }
            }
        },
    )
    ontology = load_ontology(extensions=[pack])
    # The new type can be a customer without changing customer_of in the core.
    assert "Bank" in ontology.concrete_types(ontology.relation_types["customer_of"].source)


def test_extension_cannot_redefine_core(tmp_path: Path) -> None:
    pack = write_pack(
        tmp_path, {"entity_types": {"Person": {"description": "x", "maps_to": "schema:Person"}}}
    )
    with pytest.raises(OntologyMergeError, match="'Person' is already defined"):
        load_ontology(extensions=[pack])


def test_extension_cannot_change_business_functions(tmp_path: Path) -> None:
    pack = write_pack(tmp_path, {"business_functions": ["Everything"]})
    with pytest.raises(OntologyMergeError, match="can only be set once"):
        load_ontology(extensions=[pack])
