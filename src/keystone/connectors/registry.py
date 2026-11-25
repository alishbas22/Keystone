"""Connector registry (KAN-18): find installed connectors through Python entry points.

A connector package registers itself in its pyproject.toml:

    [project.entry-points."keystone.connectors"]
    my-crm = "keystone_my_crm:MyCrmConnector"

After `pip install keystone-my-crm`, Keystone finds it automatically; no change to Keystone is
needed. The entry point name must equal the connector's `manifest.name`.
"""

from importlib.metadata import EntryPoint, entry_points
from typing import Any

from keystone.connectors.base import BaseConnector, ConnectorManifest

ENTRY_POINT_GROUP = "keystone.connectors"


class ConnectorError(Exception):
    pass


def _entry_points() -> list[EntryPoint]:
    return list(entry_points(group=ENTRY_POINT_GROUP))


def _load(entry: EntryPoint) -> type[BaseConnector[Any]]:
    try:
        cls = entry.load()
    except Exception as exc:  # a broken plugin must not break Keystone
        raise ConnectorError(f"connector '{entry.name}' failed to load: {exc}") from exc
    if not (isinstance(cls, type) and issubclass(cls, BaseConnector)):
        raise ConnectorError(f"connector '{entry.name}' ({entry.value}) is not a BaseConnector")
    manifest = getattr(cls, "manifest", None)
    if not isinstance(manifest, ConnectorManifest):
        raise ConnectorError(f"connector '{entry.name}' has no ConnectorManifest")
    if manifest.name != entry.name:
        raise ConnectorError(
            f"entry point '{entry.name}' does not match manifest name '{manifest.name}'"
        )
    return cls


def available_connectors() -> dict[str, type[BaseConnector[Any]]]:
    """All installed connectors that load correctly, by name. Broken ones are skipped."""
    found: dict[str, type[BaseConnector[Any]]] = {}
    for entry in _entry_points():
        try:
            found[entry.name] = _load(entry)
        except ConnectorError:
            continue
    return dict(sorted(found.items()))


def connector_problems() -> dict[str, str]:
    """Installed connectors that failed to load, with the reason (shown in the UI)."""
    problems: dict[str, str] = {}
    for entry in _entry_points():
        try:
            _load(entry)
        except ConnectorError as exc:
            problems[entry.name] = str(exc)
    return problems


def get_connector(name: str) -> type[BaseConnector[Any]]:
    for entry in _entry_points():
        if entry.name == name:
            return _load(entry)
    raise ConnectorError(f"no connector named '{name}'. Installed: {list(available_connectors())}")


def create_connector(
    name: str, config: dict[str, Any], instance_name: str | None = None
) -> BaseConnector[Any]:
    """Instantiate a connector by name with a configuration dict (validated by its model)."""
    return get_connector(name)(config, instance_name)
