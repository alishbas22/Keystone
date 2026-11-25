"""Check ontology mappings against the official Schema.org, W3C ORG and PROV-O definitions.

The standard vocabularies are stored in `ontology/standards/` (Turtle files) and read with
rdflib, so every `maps_to` can be verified to be a real term of the right kind.
"""

from functools import lru_cache
from pathlib import Path

from rdflib import OWL, RDF, RDFS, Graph, URIRef

from keystone.ontology.loader import ontology_dir
from keystone.ontology.models import Ontology

CLASS_TYPES = {RDFS.Class, OWL.Class}
PROPERTY_TYPES = {RDF.Property, OWL.ObjectProperty, OWL.DatatypeProperty}


@lru_cache
def _load_graph(path: Path) -> Graph:
    graph = Graph()
    graph.parse(path, format="turtle")
    return graph


def load_standards(ontology: Ontology, base_dir: Path | None = None) -> Graph:
    """All standard vocabularies the ontology declares, merged into one rdflib graph."""
    base_dir = base_dir or ontology_dir()
    merged = Graph()
    for standard in ontology.standards.values():
        merged += _load_graph(base_dir / standard.file)
    return merged


def check_mappings(ontology: Ontology, standards: Graph) -> list[str]:
    """Return a list of problems; empty means every mapping exists and has the right kind."""
    expected: dict[str, set[URIRef]] = {}
    for entity in ontology.entity_types.values():
        expected[entity.maps_to] = CLASS_TYPES
        for curie in entity.see_also:
            expected[curie] = CLASS_TYPES
    for curie in ontology.all_mapped_terms():
        expected.setdefault(curie, PROPERTY_TYPES)

    problems = []
    for curie, kinds in sorted(expected.items()):
        uri = URIRef(ontology.expand(curie))
        declared = set(standards.objects(uri, RDF.type))
        if not declared:
            problems.append(f"{curie}: not defined in the standard")
        elif not declared & kinds:
            kind = "class" if kinds is CLASS_TYPES else "property"
            problems.append(f"{curie}: exists but is not a {kind}")
    return problems
