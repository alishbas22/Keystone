"""KAN-22: the connector template generates a working, lint-clean connector."""

import os
import subprocess
import sys
import tomllib
from collections.abc import Iterator
from datetime import datetime
from importlib.metadata import EntryPoint
from pathlib import Path

import pytest
from cookiecutter.main import cookiecutter
from pydantic import BaseModel

from keystone.connectors import registry
from keystone.connectors.base import (
    BaseConnector,
    CheckResult,
    ConnectorManifest,
    Document,
    Stream,
)
from keystone.connectors.testing import check_connector

TEMPLATE = Path(__file__).resolve().parents[1] / "templates" / "connector"


@pytest.fixture(scope="module")
def project(tmp_path_factory: pytest.TempPathFactory) -> Iterator[Path]:
    out = tmp_path_factory.mktemp("generated")
    path = cookiecutter(
        str(TEMPLATE),
        no_input=True,
        output_dir=str(out),
        extra_context={
            "connector_name": "acme-crm",
            "display_name": "Acme CRM",
            "business_function": "Sales",
        },
    )
    src = str(Path(path) / "src")
    sys.path.insert(0, src)
    yield Path(path)
    sys.path.remove(src)
    for name in [m for m in sys.modules if m.startswith("keystone_acme_crm")]:
        del sys.modules[name]


def test_generated_files(project: Path) -> None:
    assert project.name == "keystone-acme-crm"
    assert (project / "src" / "keystone_acme_crm" / "connector.py").exists()
    assert (project / "tests" / "test_connector.py").exists()


def test_entry_point_matches_manifest(project: Path) -> None:
    pyproject = tomllib.loads((project / "pyproject.toml").read_text(encoding="utf-8"))
    entry_points = pyproject["project"]["entry-points"]["keystone.connectors"]
    assert entry_points == {"acme-crm": "keystone_acme_crm:AcmeCRMConnector"}


def test_generated_connector_passes_the_contract(project: Path) -> None:
    from keystone_acme_crm import AcmeCRMConnector  # type: ignore[import-not-found]

    connector = AcmeCRMConnector({"data_file": project / "sample_data" / "records.json"}, "demo")
    documents = check_connector(connector)
    assert [d.id for d in documents] == ["customers/c-1", "notes/n-1"]
    assert {d.business_function for d in documents} == {"Sales"}


def test_registry_loads_generated_connector(project: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    entry = EntryPoint("acme-crm", "keystone_acme_crm:AcmeCRMConnector", registry.ENTRY_POINT_GROUP)
    monkeypatch.setattr(registry, "_entry_points", lambda: [entry])
    assert list(registry.available_connectors()) == ["acme-crm"]
    assert registry.connector_problems() == {}


def test_generated_tests_pass(project: Path) -> None:
    result = subprocess.run(
        [sys.executable, "-m", "pytest", "-q", "-p", "no:cacheprovider", str(project / "tests")],
        cwd=project,
        env={**os.environ, "PYTHONPATH": str(project / "src")},
        capture_output=True,
        text=True,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_generated_code_is_lint_clean(project: Path) -> None:
    """Uses the generated project's own ruff settings, as its author would."""
    result = subprocess.run(
        [sys.executable, "-m", "ruff", "check", "."], cwd=project, capture_output=True, text=True
    )
    assert result.returncode == 0, result.stdout + result.stderr


# --- the contract checker catches common mistakes --------------------------------------------


class _Config(BaseModel):
    source: str = ""
    duplicate_ids: bool = False


class _Buggy(BaseConnector[_Config]):
    manifest = ConnectorManifest(name="buggy", version="0", description="x")
    config_model = _Config

    def check(self) -> CheckResult:
        return CheckResult.success()

    def discover(self) -> list[Stream]:
        return []

    def read(self, stream: str | None = None, since: datetime | None = None) -> Iterator[Document]:
        source = self.config.source or self.source_name
        yield Document(id="1", source=source, title="t", text="x")
        yield Document(
            id="1" if self.config.duplicate_ids else "2", source=source, title="t", text="y"
        )


@pytest.mark.parametrize(
    ("config", "message"),
    [
        ({"source": "someone-else"}, "Document.source must be"),
        ({"duplicate_ids": True}, "ids must be unique"),
    ],
)
def test_contract_checker_reports_mistakes(config: dict[str, object], message: str) -> None:
    with pytest.raises(AssertionError, match=message):
        check_connector(_Buggy(config))
