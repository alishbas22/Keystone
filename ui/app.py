"""Keystone web UI.

uv run streamlit run ui/app.py
"""

from pathlib import Path

import streamlit as st
from common import settings

VIEWS = Path(__file__).parent / "views"

st.set_page_config(page_title="Keystone", page_icon="🗝️", layout="wide")

PAGES = [
    ("home.py", "Ask", "🏠"),
    ("documents.py", "Documents", "📄"),
    ("explorer.py", "Graph explorer", "🕸️"),
    ("conflicts.py", "Conflicts", "⚖️"),
    ("access.py", "Sources & agents", "⚙️"),
]
navigation = st.navigation(
    [
        st.Page(str(VIEWS / file), title=title, icon=icon, default=(file == "home.py"))
        for file, title, icon in PAGES
        if (VIEWS / file).exists()
    ]
)

with st.sidebar:
    s = settings()
    key = "✅" if s.api_key else "❌ missing"
    st.caption(
        f"LLM: {s.llm_provider} ({key} key) · graph `{s.graph_group}` · "
        f"packs: {', '.join(s.ontology_packs) or 'none'}"
    )

navigation.run()
