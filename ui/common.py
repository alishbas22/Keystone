"""Shared helpers for the Keystone Streamlit pages."""

import asyncio
from collections.abc import Awaitable, Callable
from typing import TypeVar

import streamlit as st

from keystone.net import configure_network

configure_network()

from keystone.config import Settings, get_settings  # noqa: E402
from keystone.graph.engine import KeystoneGraph, open_graph  # noqa: E402
from keystone.graph.truth import Status  # noqa: E402

T = TypeVar("T")

STATUS_BADGES = {
    Status.CURRENT: "🟢 current",
    Status.HISTORY: "⚪ history",
    Status.SUPERSEDED: "🟠 superseded",
    Status.DISPUTED: "🔴 disputed",
    "current": "🟢 current",
    "history": "⚪ history",
    "superseded": "🟠 superseded",
    "disputed": "🔴 disputed",
}


def settings() -> Settings:
    return get_settings()


def run_with_graph(action: Callable[[KeystoneGraph], Awaitable[T]]) -> T:
    """Run an async graph action from Streamlit's synchronous script."""

    async def main() -> T:
        async with open_graph(settings()) as graph:
            if not st.session_state.get("graph_ready"):
                await graph.setup()
                st.session_state["graph_ready"] = True
            return await action(graph)

    return asyncio.run(main())


def require_setup() -> bool:
    """Show a clear message when the API key or Neo4j is missing. True if ready."""
    s = settings()
    if not s.api_key:
        st.error(
            f"No API key. Add `{s.api_key_name}=...` to the `.env` file in the keystone folder, "
            "then restart the app."
        )
        return False
    return True


def page_header(title: str, caption: str) -> None:
    st.set_page_config(page_title=f"Keystone · {title}", page_icon="🗝️", layout="wide")
    st.title(title)
    st.caption(caption)
