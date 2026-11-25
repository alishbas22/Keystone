"""Audit log of agent access (KAN-30).

Every MCP tool call is recorded: which agent, which tool and arguments, the agent's business
functions, when, the outcome (ok / denied / error), and the uuids of every entity and fact that
was returned. Denied calls are recorded too. Entries are stored in Neo4j next to the graph.
"""

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

from keystone.graph.engine import KeystoneGraph

AUDIT_LABEL = "KeystoneAudit"
MAX_ARGUMENT_CHARS = 2000


@dataclass(frozen=True)
class AuditEntry:
    at: datetime
    agent: str
    tool: str
    arguments: dict[str, Any]
    scope: list[str]
    outcome: str  # ok | denied | error
    returned_ids: list[str]
    message: str = ""


def returned_ids(result: Any) -> list[str]:
    """uuids of entities and facts in a tool result (keys 'uuid' and 'id', at any depth)."""
    found: list[str] = []

    def walk(value: Any) -> None:
        if isinstance(value, dict):
            for key, item in value.items():
                if key in ("uuid", "id") and isinstance(item, str) and item not in found:
                    found.append(item)
                else:
                    walk(item)
        elif isinstance(value, list):
            for item in value:
                walk(item)

    walk(result)
    return found


async def record(graph: KeystoneGraph, entry: AuditEntry) -> None:
    arguments = json.dumps(entry.arguments, default=str)[:MAX_ARGUMENT_CHARS]
    await graph.query(
        f"CREATE (:{AUDIT_LABEL} {{group_id: $group, at: $at, agent: $agent, tool: $tool, "
        f"arguments: $arguments, scope: $scope, outcome: $outcome, returned_ids: $ids, "
        f"returned: $count, message: $message}})",
        group=graph.group,
        at=entry.at,
        agent=entry.agent,
        tool=entry.tool,
        arguments=arguments,
        scope=entry.scope,
        outcome=entry.outcome,
        ids=entry.returned_ids,
        count=len(entry.returned_ids),
        message=entry.message[:500],
    )


async def read_log(
    graph: KeystoneGraph, agent: str | None = None, limit: int = 100
) -> list[AuditEntry]:
    """Most recent entries first, optionally for one agent."""
    rows = await graph.query(
        f"MATCH (a:{AUDIT_LABEL} {{group_id: $group}}) "
        f"WHERE $agent IS NULL OR a.agent = $agent "
        f"RETURN properties(a) AS a ORDER BY a.at DESC LIMIT $limit",
        group=graph.group,
        agent=agent,
        limit=limit,
    )
    entries = []
    for row in rows:
        a = dict(row["a"])  # type: ignore[call-overload]
        at = a["at"]
        entries.append(
            AuditEntry(
                at=at.to_native() if hasattr(at, "to_native") else at,
                agent=str(a["agent"]),
                tool=str(a["tool"]),
                arguments=_parse_arguments(str(a.get("arguments") or "")),
                scope=[str(s) for s in a["scope"]],
                outcome=str(a["outcome"]),
                returned_ids=[str(i) for i in a["returned_ids"]],
                message=str(a.get("message") or ""),
            )
        )
    return entries


def _parse_arguments(text: str) -> dict[str, Any]:
    try:
        value = json.loads(text) if text else {}
    except json.JSONDecodeError:  # cut at MAX_ARGUMENT_CHARS
        return {"truncated": text}
    return value if isinstance(value, dict) else {"value": value}


def now() -> datetime:
    return datetime.now(UTC)
