"""Start a Keystone MCP HTTP server inside a test, with a time limit (never hang)."""

import asyncio
import socket
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

import uvicorn

from keystone.graph.engine import KeystoneGraph
from keystone.mcp_server.server import create_server


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


@asynccontextmanager
async def mcp_http_server(graph: KeystoneGraph, start_timeout: float = 20) -> AsyncIterator[str]:
    """Yield the server's MCP URL. Fails clearly if the server crashes or does not start."""
    port = free_port()
    base = f"http://127.0.0.1:{port}"
    app = create_server(graph, http_url=base).streamable_http_app()
    server = uvicorn.Server(uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning"))
    task = asyncio.create_task(server.serve())
    loop = asyncio.get_running_loop()
    deadline = loop.time() + start_timeout
    while not server.started:
        if task.done():
            task.result()  # re-raise the server's startup error
            raise RuntimeError("MCP test server stopped before it started")
        if loop.time() > deadline:
            server.should_exit = True
            raise TimeoutError(f"MCP test server did not start within {start_timeout}s")
        await asyncio.sleep(0.05)
    try:
        yield f"{base}/mcp"
    finally:
        server.should_exit = True
        await asyncio.wait_for(task, timeout=start_timeout)
