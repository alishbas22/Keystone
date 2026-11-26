"""Two-agent demo over MCP (KAN-36): a Sales agent and a Finance agent share one Keystone.

Both connect to the same MCP server over HTTP with their own agent keys:

    demo-sales    Sales + Operations
    demo-finance  Finance + Operations

The script plays the agents' tool calls and prints what each one is allowed to see. Load the
sample company first (`uv run python demo/ask.py --load`), then:

    uv run python demo/two_agents.py

Step 5 (add_claim) calls the LLM once to extract the new fact; everything else is graph queries.
"""

import asyncio
import json
import logging
import socket
from collections.abc import Callable
from typing import Any

from keystone.net import configure_network

configure_network()

import uvicorn  # noqa: E402
from mcp import Client  # noqa: E402
from mcp.client.streamable_http import streamable_http_client  # noqa: E402
from mcp.shared._httpx_utils import create_mcp_http_client  # noqa: E402

from keystone.graph.engine import KeystoneGraph, open_graph  # noqa: E402
from keystone.mcp_server.audit import read_log  # noqa: E402
from keystone.mcp_server.auth import AgentRegistry  # noqa: E402
from keystone.mcp_server.server import create_server  # noqa: E402

AGENTS = {"demo-sales": ["Sales", "Operations"], "demo-finance": ["Finance", "Operations"]}
CLAIM = (
    "Harbor Hotels Group",
    "located_in",
    "Liverpool",
    "Daniel Price said Harbor Hotels Group moved its head office to Liverpool.",
)

Say = Callable[[str], None]


async def ensure_agents(graph: KeystoneGraph) -> dict[str, str]:
    """Create the two demo agents, or give them fresh keys if they already exist."""
    registry = AgentRegistry(graph)
    existing = {a.name for a in await registry.list_agents()}
    keys = {}
    for name, functions in AGENTS.items():
        if name in existing:
            await registry.set_functions(name, functions)
            keys[name] = await registry.rotate_key(name)
        else:
            _, keys[name] = await registry.create(name, functions)
    return keys


async def call(url: str, key: str, tool: str, **arguments: Any) -> dict[str, Any]:
    """One MCP tool call as the agent owning `key` (legacy handshake: works with every client)."""
    http = create_mcp_http_client(headers={"Authorization": f"Bearer {key}"})
    async with Client(streamable_http_client(url, http_client=http), mode="legacy") as client:
        result = await client.call_tool(tool, arguments)
    structured = getattr(result, "structured_content", None)
    if structured is not None:
        return dict(structured)
    return dict(json.loads(result.content[0].text))  # type: ignore[union-attr]


def _facts(profile: dict[str, Any], relation: str) -> list[dict[str, Any]]:
    return [
        f
        for f in profile.get("current_facts", []) + profile.get("past_facts", [])
        if f["relation"] == relation
    ]


async def run_demo(
    url: str, keys: dict[str, str], claim: tuple[str, str, str, str] = CLAIM, say: Say = print
) -> dict[str, Any]:
    """Play the demo against a running server. Returns what each agent observed."""
    sales, finance = keys["demo-sales"], keys["demo-finance"]
    seen: dict[str, Any] = {}

    say("\n1. SALES AGENT: who manages Harbor Hotels Group?")
    harbor = (await call(url, sales, "search_entities", query="Harbor Hotels"))["results"][0]
    profile = await call(url, sales, "get_entity", uuid=harbor["uuid"])
    managers = _facts(profile, "manages_account")
    for f in managers:
        sources = ", ".join(s["document"] for s in f["sources"])
        say(f"   [{f['status']}] {f['text']}  (source: {sources})")
    seen["sales_managers"] = {f["subject"]: f["status"] for f in managers}

    say("\n2. SALES AGENT: are there contradictions?")
    conflicts = (await call(url, sales, "get_conflicts"))["conflicts"]
    for c in conflicts:
        say(f"   {c['reason']} -> decided by {c['decided_by']}: {c['explanation']}")
    seen["sales_conflicts"] = [c["decided_by"] for c in conflicts]

    say("\n3. FINANCE AGENT: the same customer, through Finance's eyes")
    finance_harbor = (await call(url, finance, "search_entities", query="Harbor Hotels"))["results"]
    finance_profile = (
        await call(url, finance, "get_entity", uuid=finance_harbor[0]["uuid"])
        if finance_harbor
        else {}
    )
    finance_relations = sorted({f["relation"] for f in finance_profile.get("current_facts", [])})
    say(f"   Finance sees these relations: {finance_relations}")
    hidden = await call(
        url, finance, "get_relationships", uuid=harbor["uuid"], relation="manages_account"
    )
    count = len(hidden["facts"])
    say(f"   Account manager facts visible to Finance: {count} (Sales data is filtered out)")
    seen["finance_relations"] = finance_relations
    seen["finance_sees_managers"] = len(hidden["facts"])

    subject, relation, obj, details = claim
    say("\n4. SALES AGENT writes back what it learned (into the shared Operations function):")
    say(f"   {subject} {relation.upper()} {obj}")
    stored = await call(
        url, sales, "add_claim", subject=subject, relation=relation, object=obj,
        details=details, business_function="Operations",
    )  # fmt: skip
    say(f"   stored: {stored.get('stored')}  {stored.get('error', '')}".rstrip())
    seen["claim_stored"] = stored.get("stored")

    say("\n5. FINANCE AGENT reads the fact the Sales agent wrote (shared memory)")
    written = await call(url, finance, "get_relationships", uuid=harbor["uuid"], relation=relation)
    for f in written["facts"]:
        authors = ", ".join(s["author"] or "?" for s in f["sources"])
        say(f"   [{f['status']}] {f['text']}  (written by: {authors})")
    seen["finance_reads_claim"] = [
        s["author"]
        for f in written["facts"]
        for s in f["sources"]
        if s["source"].startswith("agent:")
    ]
    return seen


async def show_audit(graph: KeystoneGraph, say: Say = print) -> None:
    say("\n6. AUDIT LOG: every call, per agent")
    for name in AGENTS:
        entries = await read_log(graph, name, limit=50)
        tools = ", ".join(e.tool for e in reversed(entries))
        say(f"   {name}: {len(entries)} calls ({tools})")


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


async def main() -> None:
    for noisy in ("mcp", "httpx2", "httpx", "neo4j", "graphiti_core"):
        logging.getLogger(noisy).setLevel(logging.ERROR)  # keep the demo output readable
    async with open_graph() as graph:
        await graph.setup()
        keys = await ensure_agents(graph)
        port = free_port()
        base = f"http://127.0.0.1:{port}"
        app = create_server(graph, http_url=base).streamable_http_app()
        server = uvicorn.Server(
            uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        )
        task = asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.05)
        print(f"Keystone MCP server on {base}/mcp with agents {', '.join(AGENTS)}")
        try:
            await run_demo(f"{base}/mcp", keys)
            await show_audit(graph)
        finally:
            server.should_exit = True
            await task


if __name__ == "__main__":
    asyncio.run(main())
