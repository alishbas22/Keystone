"""Run the Keystone MCP server.

uv run python -m keystone.mcp_server                     # stdio, for one local agent
uv run python -m keystone.mcp_server --http --port 8765   # HTTP, for several agents
"""

import argparse

from keystone.net import configure_network


def main() -> None:
    parser = argparse.ArgumentParser(description="Keystone MCP server")
    parser.add_argument("--http", action="store_true", help="serve over HTTP (streamable-http)")
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=8765)
    args = parser.parse_args()

    configure_network()
    from keystone.mcp_server.server import create_server

    server = create_server()
    if args.http:
        server.run("streamable-http", host=args.host, port=args.port)
    else:
        server.run("stdio")


if __name__ == "__main__":
    main()
