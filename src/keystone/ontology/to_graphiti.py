"""Convert the Keystone ontology into the custom types Graphiti uses for extraction (KAN-15).

Graphiti takes Pydantic models: the class docstring tells the LLM what the type means and each
field becomes an attribute to extract. These models are generated from the YAML so the
ontology has a single source of truth.

Usage:
    schema = to_graphiti(core_ontology())
    await graphiti.add_episode(..., **schema.add_episode_kwargs())
"""

from dataclasses import dataclass
from typing import Any

from pydantic import BaseModel, Field, create_model

from keystone.ontology.models import Ontology, Property, PropertyType

# Plain JSON types work with every Graphiti backend and are easy for an LLM to fill in.
_PYTHON_TYPES: dict[PropertyType, type] = {
    PropertyType.STRING: str,
    PropertyType.TEXT: str,
    PropertyType.EMAIL: str,
    PropertyType.URL: str,
    PropertyType.DATE: str,
    PropertyType.DATETIME: str,
    PropertyType.INTEGER: int,
    PropertyType.NUMBER: float,
    PropertyType.BOOLEAN: bool,
}
_FORMAT_HINTS = {
    PropertyType.DATE: " Format: YYYY-MM-DD.",
    PropertyType.DATETIME: " Format: ISO 8601, e.g. 2026-11-25T14:30:00.",
    PropertyType.EMAIL: " Format: name@example.com.",
}


@dataclass(frozen=True)
class GraphitiSchema:
    entity_types: dict[str, type[BaseModel]]
    edge_types: dict[str, type[BaseModel]]
    edge_type_map: dict[tuple[str, str], list[str]]
    identity_properties: dict[str, list[str]]

    def add_episode_kwargs(self) -> dict[str, Any]:
        """Keyword arguments for `Graphiti.add_episode`."""
        return {
            "entity_types": self.entity_types,
            "edge_types": self.edge_types,
            "edge_type_map": self.edge_type_map,
            "identity_properties": self.identity_properties,
        }


def edge_type_name(relation: str) -> str:
    """Graphiti names relations in SCREAMING_SNAKE_CASE: 'works_for' -> 'WORKS_FOR'."""
    return relation.upper()


def _class_name(name: str) -> str:
    """'works_for' -> 'WorksFor'; 'Person' stays 'Person'."""
    return "".join(part[:1].upper() + part[1:] for part in name.split("_"))


def _model(name: str, description: str, properties: dict[str, Property]) -> type[BaseModel]:
    fields: dict[str, Any] = {
        prop_name: (
            _PYTHON_TYPES[prop.type] | None,
            Field(default=None, description=prop.description + _FORMAT_HINTS.get(prop.type, "")),
        )
        for prop_name, prop in properties.items()
    }
    model: type[BaseModel] = create_model(_class_name(name), __doc__=description, **fields)
    model.__module__ = __name__
    return model


def to_graphiti(ontology: Ontology) -> GraphitiSchema:
    entity_types = {
        name: _model(name, entity.description, entity.properties)
        for name, entity in ontology.entity_types.items()
    }

    edge_types: dict[str, type[BaseModel]] = {}
    edge_type_map: dict[tuple[str, str], list[str]] = {}
    for rel_name, rel in ontology.relation_types.items():
        edge_name = edge_type_name(rel_name)
        edge_types[edge_name] = _model(rel_name, rel.description, rel.properties)
        for source in ontology.concrete_types(rel.source):
            for target in ontology.concrete_types(rel.target):
                edge_type_map.setdefault((source, target), []).append(edge_name)

    identity_properties = {
        name: entity.identity_properties
        for name, entity in ontology.entity_types.items()
        if entity.identity_properties
    }
    return GraphitiSchema(entity_types, edge_types, edge_type_map, identity_properties)
