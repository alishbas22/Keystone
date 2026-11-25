"""Pydantic models describing the ontology YAML files and the rules every ontology must follow."""

import re
from enum import StrEnum
from typing import Annotated, Self

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


class EntityType(_Strict):
    description: str = Field(min_length=1)
    maps_to: Curie
    see_also: list[Curie] = []
    properties: dict[str, Property] = {}

    @property
    def identity_properties(self) -> list[str]:
        return [name for name, prop in self.properties.items() if prop.identity]


class RelationType(_Strict):
    description: str = Field(min_length=1)
    source: list[str] = Field(min_length=1)
    target: list[str] = Field(min_length=1)
    maps_to: Curie | None = None
    inverse: bool = False
    properties: dict[str, Property] = {}


class Ontology(_Strict):
    name: str
    version: str
    standards: dict[str, Standard] = {}
    business_functions: list[str] = Field(min_length=1)
    entity_types: dict[str, EntityType]
    relation_types: dict[str, RelationType] = {}
    provenance: dict[str, ProvenanceField] = {}

    @model_validator(mode="after")
    def _check_consistency(self) -> Self:
        errors: list[str] = []

        for type_name, entity in self.entity_types.items():
            if not PASCAL_CASE.match(type_name):
                errors.append(f"entity type '{type_name}' must be PascalCase")
            for prop_name in entity.properties:
                where = f"{type_name}.{prop_name}"
                if not SNAKE_CASE.match(prop_name):
                    errors.append(f"property '{where}' must be snake_case")
                if prop_name in GRAPHITI_NODE_FIELDS:
                    errors.append(f"property '{where}' uses a name reserved by Graphiti")

        for rel_name, rel in self.relation_types.items():
            if not SNAKE_CASE.match(rel_name):
                errors.append(f"relation '{rel_name}' must be snake_case")
            for endpoint in (*rel.source, *rel.target):
                if endpoint not in self.entity_types:
                    errors.append(f"relation '{rel_name}' refers to unknown type '{endpoint}'")
            for prop_name, prop in rel.properties.items():
                where = f"{rel_name}.{prop_name}"
                if not SNAKE_CASE.match(prop_name):
                    errors.append(f"property '{where}' must be snake_case")
                if prop_name in GRAPHITI_EDGE_FIELDS:
                    errors.append(f"property '{where}' uses a name reserved by Graphiti")
                if prop.identity:
                    errors.append(f"property '{where}': identity is only allowed on entity types")

        for curie in self.all_mapped_terms():
            prefix = curie.split(":", 1)[0]
            if prefix not in self.standards:
                errors.append(f"'{curie}' uses prefix '{prefix}' not declared under standards")

        if errors:
            raise ValueError("Invalid ontology:\n  - " + "\n  - ".join(errors))
        return self

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
