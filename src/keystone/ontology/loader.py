"""Load ontology YAML files from the `ontology/` directory."""

import os
from functools import lru_cache
from pathlib import Path

import yaml

from keystone.ontology.models import Ontology

# Repo layout: <root>/src/keystone/ontology/loader.py  ->  <root>/ontology/
_DEFAULT_DIR = Path(__file__).resolve().parents[3] / "ontology"


def ontology_dir() -> Path:
    """Directory holding core.yaml, packs/ and standards/ (override with KEYSTONE_ONTOLOGY_DIR)."""
    return Path(os.environ.get("KEYSTONE_ONTOLOGY_DIR", _DEFAULT_DIR))


def load_ontology(path: Path | None = None) -> Ontology:
    """Read and validate an ontology YAML file (default: the core ontology)."""
    path = path or ontology_dir() / "core.yaml"
    with path.open(encoding="utf-8") as f:
        data = yaml.safe_load(f)
    return Ontology.model_validate(data)


@lru_cache
def core_ontology() -> Ontology:
    """The core ontology, loaded once and cached."""
    return load_ontology()
