"""Load the ontology YAML files from the `ontology/` directory and merge them into one Ontology.

Files are combined in order: core.yaml, interfaces.yaml, rules.yaml, then any extension files
(industry packs). An extension can only ADD types, relations and rules: redefining something
that already exists is an error ("extend, don't modify").
"""

import os
from collections.abc import Iterable
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

from keystone.ontology.models import Ontology

# Repo layout: <root>/src/keystone/ontology/loader.py  ->  <root>/ontology/
_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "ontology"

CORE_FILES = ("core.yaml", "interfaces.yaml", "rules.yaml")
_NAMED_SECTIONS = ("standards", "interfaces", "entity_types", "relation_types", "provenance")
_RULE_LISTS = ("opposites", "single_valued")


class OntologyMergeError(ValueError):
    pass


def ontology_dir() -> Path:
    """Directory holding the YAML files and standards/ (override with KEYSTONE_ONTOLOGY_DIR)."""
    return Path(os.environ.get("KEYSTONE_ONTOLOGY_DIR", _DEFAULT_DIR))


def _read_yaml(path: Path) -> dict[str, Any]:
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise OntologyMergeError(f"{path.name}: expected a mapping at the top level")
    return data


def merge(base: dict[str, Any], extra: dict[str, Any], origin: str) -> dict[str, Any]:
    """Add `extra` to `base`. Named items may be added but never redefined."""
    merged = {**base}
    for key, value in extra.items():
        if key in _NAMED_SECTIONS:
            section = dict(merged.get(key) or {})
            for name, item in (value or {}).items():
                if name in section:
                    raise OntologyMergeError(
                        f"{origin}: '{name}' is already defined in {key}; extend, don't modify"
                    )
                section[name] = item
            merged[key] = section
        elif key == "rules":
            rules = dict(merged.get("rules") or {})
            for list_name, items in (value or {}).items():
                if list_name not in _RULE_LISTS:
                    raise OntologyMergeError(f"{origin}: unknown rule list '{list_name}'")
                rules[list_name] = [*rules.get(list_name, []), *(items or [])]
            merged["rules"] = rules
        elif key in merged:
            raise OntologyMergeError(f"{origin}: '{key}' can only be set once (in core.yaml)")
        else:
            merged[key] = value
    return merged


def load_raw(directory: Path | None = None, extensions: Iterable[Path] = ()) -> dict[str, Any]:
    """The merged ontology as plain data (before validation)."""
    directory = directory or ontology_dir()
    data: dict[str, Any] = {}
    for path in [*(directory / name for name in CORE_FILES), *extensions]:
        data = merge(data, _read_yaml(path), path.name)
    return data


def load_ontology(directory: Path | None = None, extensions: Iterable[Path] = ()) -> Ontology:
    """Read, merge and validate the ontology."""
    return Ontology.model_validate(load_raw(directory, extensions))


@lru_cache
def core_ontology() -> Ontology:
    """The core ontology (core + interfaces + rules), loaded once and cached."""
    return load_ontology()
