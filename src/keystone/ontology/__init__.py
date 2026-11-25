"""Load the ontology YAML (core, interfaces, rules, packs) and convert it to Graphiti types."""

from keystone.ontology.loader import core_ontology, load_ontology
from keystone.ontology.models import EntityType, Ontology, Property, RelationType

__all__ = ["EntityType", "Ontology", "Property", "RelationType", "core_ontology", "load_ontology"]
