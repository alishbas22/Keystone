"""Keystone MCP server (KAN-27): the knowledge graph as tools for any MCP-capable agent.

Every tool reads through keystone.graph.queries with the calling agent's scope, so results are
filtered by business function inside the database before the agent sees them.

Run:
    uv run python -m keystone.mcp_server                      # stdio (one local agent)
    uv run python -m keystone.mcp_server --http --port 8765    # HTTP (many agents, shared memory)
"""

import dataclasses
import os
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from datetime import date, datetime
from typing import Any

from mcp.server.auth.settings import AuthSettings
from mcp.server.mcpserver import MCPServer
from mcp.server.mcpserver.exceptions import ToolError

from keystone import __version__
from keystone.graph import claims, queries
from keystone.graph.engine import KeystoneGraph, open_graph
from keystone.mcp_server import audit
from keystone.mcp_server.auth import (
    AccessDenied,
    AgentIdentity,
    AgentRegistry,
    KeystoneTokenVerifier,
    current_agent,
)
from keystone.ontology import Ontology

AGENT_KEY_ENV = "KEYSTONE_AGENT_KEY"
NOT_VISIBLE = "not found or not visible to this agent"


async def _stdio_agent(registry: AgentRegistry) -> AgentIdentity:
    """The single agent of a stdio server, from the KEYSTONE_AGENT_KEY environment variable."""
    key = os.environ.get(AGENT_KEY_ENV, "")
    agent = await registry.verify(key) if key else None
    if agent is None:
        raise AccessDenied(
            f"{AGENT_KEY_ENV} is missing or not a valid agent key. Create one with "
            f"`python -m keystone.mcp_server.agents create <name> --functions Sales`."
        )
    return agent


INSTRUCTIONS = """\
Keystone is a company knowledge graph built from documents, emails and databases, mapped onto a
standard business ontology. Every fact has a source, a date and a status:
current (true now), history (ended), superseded (lost to a newer or more authoritative source) or
disputed (contradicted, unresolved).

How to use it:
1. describe_ontology to learn the entity types and relations.
2. search_entities to find an entity's uuid by name.
3. get_entity / get_relationships for facts; find_path to see how two entities are linked.
4. get_conflicts to see contradictions; explain_claim to cite a fact's source documents.
5. add_claim to store something you learned; other agents with access will see it.
Base answers on current facts and cite their sources. Mention when something changed over time.
You only see data for the business functions your agent key allows."""


def to_json(value: Any) -> Any:
    """Dataclasses, dates and containers -> JSON-friendly values."""
    if dataclasses.is_dataclass(value) and not isinstance(value, type):
        return {k: to_json(v) for k, v in dataclasses.asdict(value).items()}
    if isinstance(value, datetime | date):
        return value.isoformat()
    if isinstance(value, dict):
        return {str(k): to_json(v) for k, v in value.items()}
    if isinstance(value, list | tuple | set):
        return [to_json(v) for v in value]
    return value


def describe(ontology: Ontology) -> dict[str, Any]:
    """The ontology in a compact form an agent can plan with."""
    return {
        "business_functions": ontology.business_functions,
        "packs": [p.name for p in ontology.packs],
        "entity_types": {
            name: {
                "description": e.description,
                "standard": e.maps_to,
                "interfaces": e.implements,
                "properties": {p: prop.description for p, prop in e.properties.items()},
            }
            for name, e in ontology.entity_types.items()
        },
        "interfaces": {
            name: {"description": i.description, "implemented_by": ontology.implementers(name)}
            for name, i in ontology.interfaces.items()
        },
        "relations": {
            name: {
                "description": r.description,
                "from": ontology.concrete_types(r.source),
                "to": ontology.concrete_types(r.target),
                "properties": {p: prop.description for p, prop in r.properties.items()},
            }
            for name, r in ontology.relation_types.items()
        },
        "rules": {
            "opposites": [list(pair) for pair in ontology.rules.opposites],
            "single_valued": [
                {"relation": r.relation, "per": r.per, "meaning": r.description}
                for r in ontology.rules.single_valued
            ],
            "source_authority_most_trusted_first": ontology.rules.source_authority,
        },
    }


class _State:
    graph: KeystoneGraph | None = None
    agent: AgentIdentity | None = None


def create_server(
    graph: KeystoneGraph | None = None,
    agent: AgentIdentity | None = None,
    http_url: str | None = None,
) -> MCPServer:
    """Build the server.

    - stdio: the agent comes from KEYSTONE_AGENT_KEY (checked at startup), or `agent` in tests.
    - HTTP (`http_url`, e.g. "http://127.0.0.1:8765"): every request must carry an agent key
      as a bearer token; each agent sees only its business functions.
    """
    state = _State()
    state.graph, state.agent = graph, agent
    verifier = KeystoneTokenVerifier()

    @asynccontextmanager
    async def lifespan(_: MCPServer) -> AsyncIterator[None]:
        if state.graph is not None:
            verifier.registry = AgentRegistry(state.graph)
            yield
            return
        async with open_graph() as opened:
            await opened.setup()
            state.graph = opened
            verifier.registry = AgentRegistry(opened)
            if http_url is None and state.agent is None:
                state.agent = await _stdio_agent(verifier.registry)
            try:
                yield
            finally:
                state.graph = None

    auth = (
        AuthSettings(
            issuer_url=http_url,  # type: ignore[arg-type]
            resource_server_url=f"{http_url}/mcp",  # type: ignore[arg-type]
            validate_token_resource=False,  # agent keys are issued by Keystone for this server only
        )
        if http_url
        else None
    )
    server = MCPServer(
        name="keystone",
        title="Keystone business knowledge graph",
        version=__version__,
        instructions=INSTRUCTIONS,
        lifespan=lifespan,
        token_verifier=verifier if http_url else None,
        auth=auth,
    )

    async def run(
        tool: str,
        arguments: dict[str, Any],
        action: Callable[[KeystoneGraph, AgentIdentity], Awaitable[dict[str, Any]]],
    ) -> dict[str, Any]:
        """Resolve the calling agent, run the tool and write an audit entry (KAN-30)."""
        assert state.graph is not None, "graph not open"
        graph = state.graph
        try:
            agent = current_agent(graph.ontology, state.agent)
        except AccessDenied as exc:
            denied = audit.AuditEntry(
                audit.now(), "unknown", tool, arguments, [], "denied", [], str(exc)
            )
            await audit.record(graph, denied)
            raise ToolError(f"Access denied: {exc}") from exc  # shown to the agent
        try:
            result = await action(graph, agent)
        except Exception as exc:
            failed = audit.AuditEntry(
                audit.now(), agent.name, tool, arguments, agent.scope, "error", [], repr(exc)
            )
            await audit.record(graph, failed)
            raise
        done = audit.AuditEntry(
            audit.now(), agent.name, tool, arguments, agent.scope, "ok",
            audit.returned_ids(result), str(result.get("error", "")),
        )  # fmt: skip
        await audit.record(graph, done)
        return result

    @server.tool()
    async def search_entities(query: str, limit: int = 10) -> dict[str, Any]:
        """Find people, organisations, products, orders and other entities by name or alias.

        Returns uuids to use with get_entity, get_relationships and find_path.
        """

        async def action(graph: KeystoneGraph, agent: AgentIdentity) -> dict[str, Any]:
            found = await queries.search_entities(graph, query, agent.scope, max(1, min(limit, 50)))
            return {"results": to_json(found)}

        return await run("search_entities", {"query": query, "limit": limit}, action)

    @server.tool()
    async def get_entity(uuid: str) -> dict[str, Any]:
        """Full profile of an entity: type, properties, aliases, current facts and past facts
        (history, superseded or disputed), each with its sources."""

        async def action(graph: KeystoneGraph, agent: AgentIdentity) -> dict[str, Any]:
            profile = await queries.get_entity(graph, uuid, agent.scope)
            return to_json(profile) if profile else {"error": NOT_VISIBLE}

        return await run("get_entity", {"uuid": uuid}, action)

    @server.tool()
    async def get_relationships(uuid: str, relation: str | None = None) -> dict[str, Any]:
        """Every fact connecting an entity to others, optionally only one relation type
        (e.g. "manages_account"). Includes status and sources."""

        async def action(graph: KeystoneGraph, agent: AgentIdentity) -> dict[str, Any]:
            facts = await queries.get_relationships(graph, uuid, agent.scope, relation)
            return {"facts": to_json(facts)}

        return await run("get_relationships", {"uuid": uuid, "relation": relation}, action)

    @server.tool()
    async def find_path(from_uuid: str, to_uuid: str, max_hops: int = 4) -> dict[str, Any]:
        """The shortest chain of facts linking two entities (up to max_hops, at most 6)."""

        async def action(graph: KeystoneGraph, agent: AgentIdentity) -> dict[str, Any]:
            path = await queries.find_path(graph, from_uuid, to_uuid, agent.scope, max_hops)
            return {"path": to_json(path), "found": bool(path)}

        arguments = {"from_uuid": from_uuid, "to_uuid": to_uuid, "max_hops": max_hops}
        return await run("find_path", arguments, action)

    @server.tool()
    async def get_conflicts(only_open: bool = False) -> dict[str, Any]:
        """Contradictions between facts (e.g. two different account managers for one customer),
        with the rule that resolved each one: person, time, source authority, corroboration,
        or none (still disputed)."""

        async def action(graph: KeystoneGraph, agent: AgentIdentity) -> dict[str, Any]:
            found = await queries.get_conflicts(graph, agent.scope, include_resolved=not only_open)
            return {
                "conflicts": [{**to_json(c), "open": c.open} for c in found],
                "open": sum(1 for c in found if c.open),
            }

        return await run("get_conflicts", {"only_open": only_open}, action)

    @server.tool()
    async def explain_claim(fact_id: str) -> dict[str, Any]:
        """Where a fact came from: source documents, sections, pages, authors, dates,
        confidence, plus its status and any contradicting facts. Use it to cite answers."""

        async def action(graph: KeystoneGraph, agent: AgentIdentity) -> dict[str, Any]:
            fact = await queries.explain_claim(graph, fact_id, agent.scope)
            return to_json(fact) if fact else {"error": NOT_VISIBLE}

        return await run("explain_claim", {"fact_id": fact_id}, action)

    @server.tool()
    async def add_claim(
        subject: str,
        relation: str,
        object: str,
        details: str = "",
        business_function: str | None = None,
    ) -> dict[str, Any]:
        """Store something you learned, e.g. subject="Daniel Price", relation="works_for",
        object="Harbor Hotels Group". `relation` must be an ontology relation (see
        describe_ontology); `details` is optional context. The claim is attributed to you,
        dated now, and visible to other agents with access to the business function (default:
        your first one). Returns the stored facts and any existing facts they contradict."""

        async def action(graph: KeystoneGraph, agent: AgentIdentity) -> dict[str, Any]:
            try:
                result = await claims.add_claim(
                    graph, agent.name, agent.scope, subject, relation, object, details,
                    business_function,
                )  # fmt: skip
            except claims.ClaimRejected as exc:
                return {"stored": False, "error": str(exc)}
            return {"stored": True, **to_json(result)}

        arguments = {
            "subject": subject, "relation": relation, "object": object, "details": details,
            "business_function": business_function,
        }  # fmt: skip
        return await run("add_claim", arguments, action)

    @server.tool()
    async def describe_ontology() -> dict[str, Any]:
        """The business concepts (entity types, relations, interfaces) and contradiction rules,
        plus which business functions this agent can see. Call this first to plan queries."""

        async def action(graph: KeystoneGraph, agent: AgentIdentity) -> dict[str, Any]:
            return {**describe(graph.ontology), "your_access": to_json(agent)}

        return await run("describe_ontology", {}, action)

    return server
