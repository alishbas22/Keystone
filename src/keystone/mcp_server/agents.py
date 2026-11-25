"""Manage agent keys from the command line.

uv run python -m keystone.mcp_server.agents create sales-bot --functions Sales
uv run python -m keystone.mcp_server.agents list
uv run python -m keystone.mcp_server.agents set-functions sales-bot --functions Sales Finance
uv run python -m keystone.mcp_server.agents revoke sales-bot
"""

import argparse
import asyncio
from collections.abc import Sequence

from keystone.graph.engine import open_graph
from keystone.mcp_server.auth import AgentRegistry


async def run(args: argparse.Namespace) -> None:
    async with open_graph() as graph:
        await graph.setup()
        registry = AgentRegistry(graph)
        if args.command == "create":
            agent, key = await registry.create(args.name, args.functions)
            print(f"Created agent '{agent.name}' with access to: {', '.join(agent.scope)}")
            print(f"\n  Agent key (shown only once, store it safely):\n\n  {key}\n")
        elif args.command == "list":
            for record in await registry.list_agents():
                state = "active" if record.active else "revoked"
                print(
                    f"{record.name:<20} {state:<8} {', '.join(record.functions):<30} "
                    f"key {record.key_hint}…  created {record.created_at:%Y-%m-%d}"
                )
        elif args.command == "set-functions":
            await registry.set_functions(args.name, args.functions)
            print(f"'{args.name}' can now see: {', '.join(args.functions)}")
        elif args.command == "revoke":
            await registry.revoke(args.name)
            print(f"Key for '{args.name}' revoked.")


def main(argv: Sequence[str] | None = None) -> None:
    parser = argparse.ArgumentParser(description="Manage Keystone agent keys")
    sub = parser.add_subparsers(dest="command", required=True)
    create = sub.add_parser("create", help="create an agent and print its key once")
    create.add_argument("name")
    create.add_argument("--functions", nargs="+", required=True, help="e.g. Sales Finance")
    sub.add_parser("list", help="list agents")
    update = sub.add_parser("set-functions", help="change what an agent may see")
    update.add_argument("name")
    update.add_argument("--functions", nargs="+", required=True)
    revoke = sub.add_parser("revoke", help="disable an agent's key")
    revoke.add_argument("name")
    asyncio.run(run(parser.parse_args(argv)))


if __name__ == "__main__":
    from keystone.net import configure_network

    configure_network()
    main()
