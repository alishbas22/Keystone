"""Agent keys tied to business functions (KAN-29).

Each agent has a secret key. Keystone stores only the key's SHA-256 hash, the agent's name and
the business functions it may see. The key is shown once, when the agent is created.

- HTTP: agents send `Authorization: Bearer <key>`; KeystoneTokenVerifier checks it (MCP auth).
- stdio: the server is started with KEYSTONE_AGENT_KEY for the single local agent.
- No valid key means no access: there is no "see everything" fallback.
"""

import hashlib
import re
import secrets
from contextvars import ContextVar
from dataclasses import dataclass
from datetime import UTC, datetime

from mcp.server.auth.middleware.auth_context import get_access_token
from mcp.server.auth.provider import AccessToken

from keystone.graph.engine import KeystoneGraph
from keystone.ontology import Ontology

AGENT_LABEL = "KeystoneAgent"
KEY_PREFIX = "ks_"
_NAME = re.compile(r"^[a-z][a-z0-9_-]{1,40}$")


class AccessDenied(PermissionError):
    pass


@dataclass(frozen=True)
class AgentIdentity:
    name: str
    scope: list[str]  # business functions this agent may see and write


@dataclass(frozen=True)
class AgentRecord:
    name: str
    functions: list[str]
    active: bool
    created_at: datetime
    key_hint: str  # first characters of the key, to recognise it


def hash_key(key: str) -> str:
    return hashlib.sha256(key.encode()).hexdigest()


class AgentRegistry:
    """Agents and their keys, stored in Neo4j next to the graph they may read."""

    def __init__(self, graph: KeystoneGraph):
        self.graph = graph

    def _check_functions(self, functions: list[str]) -> list[str]:
        allowed = self.graph.ontology.business_functions
        unknown = [f for f in functions if f not in allowed]
        if not functions or unknown:
            raise ValueError(f"business functions must be a non-empty subset of {allowed}")
        return [f for f in allowed if f in functions]  # canonical order, no duplicates

    async def create(self, name: str, functions: list[str]) -> tuple[AgentIdentity, str]:
        """Create an agent and return its key. The key is not stored and cannot be shown again."""
        if not _NAME.match(name):
            raise ValueError(
                "agent names are lowercase letters, digits, '-' or '_', e.g. 'sales-bot'"
            )
        functions = self._check_functions(functions)
        if any(a.name == name for a in await self.list_agents()):
            raise ValueError(f"an agent named '{name}' already exists")
        key = KEY_PREFIX + secrets.token_urlsafe(32)
        await self.graph.query(
            f"CREATE (:{AGENT_LABEL} {{group_id: $group, name: $name, functions: $functions, "
            f"key_hash: $hash, key_hint: $hint, active: true, created_at: $now}})",
            group=self.graph.group,
            name=name,
            functions=functions,
            hash=hash_key(key),
            hint=key[:7],
            now=datetime.now(UTC),
        )
        return AgentIdentity(name, functions), key

    async def list_agents(self) -> list[AgentRecord]:
        rows = await self.graph.query(
            f"MATCH (a:{AGENT_LABEL} {{group_id: $group}}) "
            f"RETURN properties(a) AS a ORDER BY a.name",
            group=self.graph.group,
        )
        records = []
        for row in rows:
            a = dict(row["a"])  # type: ignore[call-overload]
            created = a["created_at"]
            records.append(
                AgentRecord(
                    name=str(a["name"]),
                    functions=[str(f) for f in a["functions"]],
                    active=bool(a["active"]),
                    created_at=created.to_native() if hasattr(created, "to_native") else created,
                    key_hint=str(a["key_hint"]),
                )
            )
        return records

    async def set_functions(self, name: str, functions: list[str]) -> None:
        await self._update(name, functions=self._check_functions(functions))

    async def revoke(self, name: str) -> None:
        """The key stops working immediately. The agent's past writes stay attributed to it."""
        await self._update(name, active=False)

    async def _update(self, name: str, **changes: object) -> None:
        rows = await self.graph.query(
            f"MATCH (a:{AGENT_LABEL} {{group_id: $group, name: $name}}) SET a += $changes "
            f"RETURN a.name AS name",
            group=self.graph.group,
            name=name,
            changes=changes,
        )
        if not rows:
            raise ValueError(f"no agent named '{name}'")

    async def verify(self, key: str) -> AgentIdentity | None:
        """The agent a key belongs to, or None if the key is unknown or revoked."""
        if not key.startswith(KEY_PREFIX):
            return None
        rows = await self.graph.query(
            f"MATCH (a:{AGENT_LABEL} {{group_id: $group, key_hash: $hash, active: true}}) "
            f"RETURN a.name AS name, a.functions AS functions",
            group=self.graph.group,
            hash=hash_key(key),
        )
        if not rows:
            return None
        functions = [str(f) for f in rows[0]["functions"]]  # type: ignore[attr-defined]
        return AgentIdentity(str(rows[0]["name"]), functions)


class KeystoneTokenVerifier:
    """MCP TokenVerifier: an agent key in `Authorization: Bearer ...` -> the agent's identity."""

    def __init__(self, registry: "AgentRegistry | None" = None):
        self.registry = registry

    async def verify_token(self, token: str) -> AccessToken | None:
        if self.registry is None:
            return None
        agent = await self.registry.verify(token)
        if agent is None:
            return None
        return AccessToken(token=token, client_id=agent.name, scopes=agent.scope)


# The agent for stdio servers (set at startup from KEYSTONE_AGENT_KEY) or for tests.
fixed_agent: ContextVar[AgentIdentity | None] = ContextVar("keystone_fixed_agent", default=None)


def current_agent(ontology: Ontology, fixed: AgentIdentity | None = None) -> AgentIdentity:
    """Who is calling: the HTTP bearer token's agent, else the server's fixed agent. Else deny."""
    token = get_access_token()
    if token is not None:
        scope = [f for f in ontology.business_functions if f in token.scopes]
        return AgentIdentity(token.client_id, scope)
    agent = fixed or fixed_agent.get()
    if agent is None:
        raise AccessDenied("no agent key: start the server with KEYSTONE_AGENT_KEY or send a key")
    return AgentIdentity(agent.name, [f for f in ontology.business_functions if f in agent.scope])
