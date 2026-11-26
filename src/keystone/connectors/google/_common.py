"""Helpers shared by the Google connectors."""

from collections.abc import Iterable
from datetime import UTC, datetime

BUSINESS_FUNCTIONS = ("Sales", "Finance", "HR", "Operations")


def function_from_names(names: Iterable[str], default: str | None) -> str | None:
    """A business function named by a folder or label (case-insensitive), else the default."""
    for name in names:
        for function in BUSINESS_FUNCTIONS:
            if name.strip().lower() == function.lower():
                return function
    return default


def parse_rfc3339(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
