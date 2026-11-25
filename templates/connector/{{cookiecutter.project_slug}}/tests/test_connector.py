from datetime import UTC, datetime
from pathlib import Path

from keystone.connectors.testing import check_connector

from {{ cookiecutter.package_name }} import {{ cookiecutter.class_name }}

SAMPLE = Path(__file__).resolve().parents[1] / "sample_data" / "records.json"


def make() -> {{ cookiecutter.class_name }}:
    return {{ cookiecutter.class_name }}({"data_file": SAMPLE}, "test")


def test_follows_the_keystone_connector_contract() -> None:
    documents = check_connector(make())
    assert len(documents) == 2


def test_streams() -> None:
    assert [s.name for s in make().discover()] == ["customers", "notes"]


def test_incremental_read() -> None:
    since = datetime(2026, 3, 31, tzinfo=UTC)
    assert [d.id for d in make().read(since=since)] == ["notes/n-1"]


def test_missing_file_is_reported() -> None:
    connector = {{ cookiecutter.class_name }}({"data_file": "missing.json"})
    assert not connector.check().ok
