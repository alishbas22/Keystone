"""Pydantic models describing the ontology YAML files and the rules every ontology must follow."""

import re
from enum import StrEnum
from typing import Annotated, Literal, NamedTuple, Self

from pydantic import AfterValidator, BaseModel, ConfigDict, Field, model_validator

PASCAL_CASE = re.compile(r"^[A-Z][A-Za-z0-9]*$")
SNAKE_CASE = re.compile(r"^[a-z][a-z0-9_]*$")
CURIE = re.compile(r"^([a-z][a-z0-9_-]*):([A-Za-z][A-Za-z0-9_-]*)$")

# Field names Graphiti uses on its own nodes/edges (graphiti_core.nodes.EntityNode and
# graphiti_core.edges.EntityEdge). Graphiti rejects custom types that reuse them.
GRAPHITI_NODE_FIELDS = frozenset(
    {"uuid", "name", "group_id", "labels", "created_at", "summary", "attributes", "name_embedding"}
)
GRAPHITI_EDGE_FIELDS = frozenset(
    {
        "uuid", "name", "group_id", "created_at", "attributes", "fact", "fact_embedding",
        "episodes", "expired_at", "valid_at", "invalid_at", "reference_time",
        "source_node_uuid", "target_node_uuid",
    }
)  # fmt: skip


class PropertyType(StrEnum):
    STRING = "string"
    TEXT = "text"
    INTEGER = "integer"
    NUMBER = "number"
    BOOLEAN = "boolean"
    DATE = "date"
    DATETIME = "datetime"
    EMAIL = "email"
    URL = "url"


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


def _check_curie(value: str) -> str:
    if not CURIE.match(value):
        raise ValueError(f"'{value}' must look like 'prefix:Term', e.g. 'schema:Person'")
    return value


# A compact standard term reference such as "schema:Person" (prefix declared under `standards`).
Curie = Annotated[str, AfterValidator(_check_curie)]


class Standard(_Strict):
    """An external vocabulary we map onto (Schema.org, W3C ORG, PROV-O)."""

    namespace: str
    file: str
    title: str


class Property(_Strict):
    type: PropertyType
    description: str = Field(min_length=1)
    maps_to: Curie | None = None
    identity: bool = False


class ProvenanceField(Property):
    required: bool = True


class Interface(_Strict):
    """A shared label for entity types that play the same role (e.g. Party = Person or Org)."""

    description: str = Field(min_length=1)
    properties: dict[str, Property] = {}


class EntityType(_Strict):
    description: str = Field(min_length=1)
    maps_to: Curie
    see_also: list[Curie] = []
    implements: list[str] = []
    properties: dict[str, Property] = {}

    @property
    def identity_properties(self) -> list[str]:
        return [name for name, prop in self.properties.items() if prop.identity]


class RelationType(_Strict):
    description: str = Field(min_length=1)
    source: list[str] = Field(min_length=1)  # entity type or interface names
    target: list[str] = Field(min_length=1)
    maps_to: Curie | None = None
    inverse: bool = False
    properties: dict[str, Property] = {}


# --- Relation rules (used by the truth engine to detect contradictions) -----------------------


class SingleValuedRule(_Strict):
    relation: str
    per: Literal["source", "target"]
    description: str = Field(min_length=1)


class Triple(NamedTuple):
    """A minimal claim: (subject, relation, object), with entities already resolved."""

    subject: str
    relation: str
    object: str


class RelationRules(_Strict):
    opposites: list[tuple[str, str]] = []
    single_valued: list[SingleValuedRule] = []

    def opposite_of(self, relation: str) -> str | None:
        for a, b in self.opposites:
            if relation == a:
                return b
            if relation == b:
                return a
        return None

    def single_valued_rule(self, relation: str) -> SingleValuedRule | None:
        return next((r for r in self.single_valued if r.relation == relation), None)

    def contradicts(self, a: Triple, b: Triple) -> str | None:
        """Why two claims that hold at the same time contradict each other, or None.

        Only the ontology rules are checked here. Whether the claims overlap in time, and which
        one wins, is decided by the truth engine.
        """
        same_pair = (a.subject, a.object) == (b.subject, b.object)
        if same_pair and self.opposite_of(a.relation) == b.relation:
            return f"'{a.relation}' and '{b.relation}' are opposites"
        if a.relation != b.relation:
            return None
        rule = self.single_valued_rule(a.relation)
        if rule is None:
            return None
        if rule.per == "source" and a.subject == b.subject and a.object != b.object:
            return f"'{a.relation}' allows only one value: {rule.description}"
        if rule.per == "target" and a.object == b.object and a.subject != b.subject:
            return f"'{a.relation}' allows only one value: {rule.description}"
        return None


# --- The whole ontology -----------------------------------------------------------------------


class PackInfo(_Strict):
    """Header of an industry pack file."""

    name: str
    version: str
    description: str = Field(min_length=1)


class Ontology(_Strict):
    name: str
    version: str
    packs: list[PackInfo] = []
    standards: dict[str, Standard] = {}
    business_functions: list[str] = Field(min_length=1)
    interfaces: dict[str, Interface] = {}
    entity_types: dict[str, EntityType]
    relation_types: dict[str, RelationType] = {}
    rules: RelationRules = RelationRules()
    provenance: dict[str, ProvenanceField] = {}

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        errors = [
            *self._interface_errors(),
            *self._entity_errors(),
            *self._relation_errors(),
            *self._rule_errors(),
            *self._prefix_errors(),
        ]
        if errors:
            raise ValueError("Invalid ontology:\n  - " + "\n  - ".join(errors))
        return self

    # --- queries -------------------------------------------------------------------------------

    def implementers(self, interface: str) -> list[str]:
        """Entity types that implement an interface, in definition order."""
        return [name for name, e in self.entity_types.items() if interface in e.implements]

    def concrete_types(self, names: list[str]) -> list[str]:
        """Expand interface names into the entity types implementing them (no duplicates)."""
        result: list[str] = []
        for name in names:
            for concrete in self.implementers(name) if name in self.interfaces else [name]:
                if concrete not in result:
                    result.append(concrete)
        return result

    def all_mapped_terms(self) -> set[str]:
        """Every `prefix:Term` the ontology points to."""
        terms: set[str] = set()
        for entity in self.entity_types.values():
            terms.update([entity.maps_to, *entity.see_also])
            terms.update(p.maps_to for p in entity.properties.values() if p.maps_to)
        for rel in self.relation_types.values():
            if rel.maps_to:
                terms.add(rel.maps_to)
            terms.update(p.maps_to for p in rel.properties.values() if p.maps_to)
        terms.update(p.maps_to for p in self.provenance.values() if p.maps_to)
        return terms

    def expand(self, curie: str) -> str:
        """'schema:Person' -> 'https://schema.org/Person'."""
        prefix, local = curie.split(":", 1)
        return self.standards[prefix].namespace + local

    # --- validation helpers --------------------------------------------------------------------

    @staticmethod
    def _property_errors(
        owner: str, props: dict[str, Property], reserved: frozenset[str]
    ) -> list[str]:
        errors = []
        for prop_name in props:
            where = f"{owner}.{prop_name}"
            if not SNAKE_CASE.match(prop_name):
                errors.append(f"property '{where}' must be snake_case")
            if prop_name in reserved:
                errors.append(f"property '{where}' uses a name reserved by Graphiti")
        return errors

    def _interface_errors(self) -> list[str]:
        errors = []
        for name, interface in self.interfaces.items():
            if not PASCAL_CASE.match(name):
                errors.append(f"interface '{name}' must be PascalCase")
            if name in self.entity_types:
                errors.append(f"'{name}' is both an interface and an entity type")
            errors += self._property_errors(name, interface.properties, GRAPHITI_NODE_FIELDS)
        return errors

    def _entity_errors(self) -> list[str]:
        errors = []
        for name, entity in self.entity_types.items():
            if not PASCAL_CASE.match(name):
                errors.append(f"entity type '{name}' must be PascalCase")
            errors += self._property_errors(name, entity.properties, GRAPHITI_NODE_FIELDS)
            for interface_name in entity.implements:
                interface = self.interfaces.get(interface_name)
                if interface is None:
                    errors.append(f"'{name}' implements unknown interface '{interface_name}'")
                    continue
                for prop_name, prop in interface.properties.items():
                    own = entity.properties.get(prop_name)
                    if own is None or own.type != prop.type:
                        errors.append(
                            f"'{name}' implements '{interface_name}' so it needs property "
                            f"'{prop_name}' of type '{prop.type}'"
                        )
        return errors

    def _relation_errors(self) -> list[str]:
        errors = []
        for name, rel in self.relation_types.items():
            if not SNAKE_CASE.match(name):
                errors.append(f"relation '{name}' must be snake_case")
            for endpoint in (*rel.source, *rel.target):
                if endpoint not in self.entity_types and endpoint not in self.interfaces:
                    errors.append(f"relation '{name}' refers to unknown type '{endpoint}'")
            errors += self._property_errors(name, rel.properties, GRAPHITI_EDGE_FIELDS)
            for prop_name, prop in rel.properties.items():
                if prop.identity:
                    errors.append(
                        f"property '{name}.{prop_name}': identity is only allowed on entity types"
                    )
        return errors

    def _rule_errors(self) -> list[str]:
        errors = []
        seen: set[str] = set()
        for a, b in self.rules.opposites:
            for rel in (a, b):
                if rel not in self.relation_types:
                    errors.append(f"opposite rule refers to unknown relation '{rel}'")
                elif rel in seen:
                    errors.append(f"relation '{rel}' appears in more than one opposite rule")
                seen.add(rel)
            if a == b:
                errors.append(f"relation '{a}' cannot be its own opposite")
            elif a in self.relation_types and b in self.relation_types:
                ra, rb = self.relation_types[a], self.relation_types[b]
                same_source = self.concrete_types(ra.source) == self.concrete_types(rb.source)
                same_target = self.concrete_types(ra.target) == self.concrete_types(rb.target)
                if not (same_source and same_target):
                    errors.append(f"opposites '{a}' and '{b}' must connect the same types")

        names = [r.relation for r in self.rules.single_valued]
        for rel in names:
            if rel not in self.relation_types:
                errors.append(f"single-valued rule refers to unknown relation '{rel}'")
        for rel in sorted({r for r in names if names.count(r) > 1}):
            errors.append(f"relation '{rel}' has more than one single-valued rule")
        return errors

    def _prefix_errors(self) -> list[str]:
        errors = []
        for curie in sorted(self.all_mapped_terms()):
            prefix = curie.split(":", 1)[0]
            if prefix not in self.standards:
                errors.append(f"'{curie}' uses prefix '{prefix}' not declared under standards")
        return errors
