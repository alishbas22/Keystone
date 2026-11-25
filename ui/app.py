"""Keystone UI: upload documents, ask a question, see the answer with its sources.

uv run streamlit run ui/app.py
"""

import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import TypeVar

import streamlit as st

from keystone.net import configure_network

configure_network()

from keystone.config import get_settings  # noqa: E402
from keystone.connectors import Document  # noqa: E402
from keystone.connectors.folder import FolderConnector  # noqa: E402
from keystone.graph.engine import KeystoneGraph, open_graph  # noqa: E402
from keystone.graph.qa import Answer, answer_question  # noqa: E402
from keystone.graph.truth import Status  # noqa: E402
from keystone.ingest.parse import SUPPORTED_SUFFIXES, ParsedDocument, parse_document  # noqa: E402
from keystone.ingest.pipeline import ingest_document  # noqa: E402
from keystone.ontology import active_ontology  # noqa: E402

SAMPLE_DIR = Path(__file__).resolve().parents[1] / "demo" / "data"
# Which business function owns each sample document.
SAMPLE_FUNCTIONS = {
    "company_overview": "HR",
    "account_plan": "Sales",
    "customer_report": "Sales",
    "finance_review": "Finance",
    "reassignment_memo": "Sales",
}
STATUS_BADGES = {
    Status.CURRENT: "🟢 current",
    Status.HISTORY: "⚪ history",
    Status.SUPERSEDED: "🟠 superseded",
    Status.DISPUTED: "🔴 disputed",
}
EXAMPLE_QUESTION = "Who is our biggest customer and who manages their account?"

T = TypeVar("T")
settings = get_settings()
functions = active_ontology().business_functions

st.set_page_config(page_title="Keystone", page_icon="🗝️", layout="wide")


def run_with_graph(action: Callable[[KeystoneGraph], Awaitable[T]]) -> T:
    """Run an async graph action from Streamlit's synchronous script."""

    async def main() -> T:
        async with open_graph(settings) as graph:
            if not st.session_state.get("graph_ready"):
                await graph.setup()
                st.session_state["graph_ready"] = True
            return await action(graph)

    return asyncio.run(main())


@st.cache_data(show_spinner=False)
def preview(_document: Document, content_hash: str) -> ParsedDocument:
    return parse_document(_document)


def ingest(items: list[tuple[Document, str]]) -> None:
    status = st.status("Ingesting documents…", expanded=True)

    async def work(graph: KeystoneGraph) -> None:
        for document, function in items:
            status.write(f"📄 **{document.title}**")
            result = await ingest_document(graph, document, function, status.caption)
            status.write(f"→ {result.status} {result.message}".strip())

    try:
        run_with_graph(work)
        status.update(label="Ingestion finished", state="complete")
    except Exception as exc:
        status.update(label="Ingestion failed", state="error")
        st.exception(exc)


# --- Sidebar: setup checks ---------------------------------------------------------------------

with st.sidebar:
    st.header("Setup")
    key_ok = bool(settings.api_key)
    if key_ok:
        st.success(f"{settings.llm_provider.title()} API key found")
    else:
        st.error(
            f"No API key. Add `{settings.api_key_name}=...` to the `.env` file in the keystone "
            "folder, then restart this app."
        )
    st.caption(f"Neo4j: `{settings.neo4j_uri}` · graph: `{settings.graph_group}`")
    st.divider()
    with st.expander("Danger zone"):
        confirm = st.checkbox("Yes, delete everything in the graph")
        if st.button("Delete all data", disabled=not (confirm and key_ok)):
            run_with_graph(lambda g: g.delete_all())
            st.success("Graph cleared.")

st.title("🗝️ Keystone")
st.caption("Every fact has a source. Every answer has proof.")

docs_tab, ask_tab = st.tabs(["1 · Documents", "2 · Ask a question"])

# --- Tab 1: documents --------------------------------------------------------------------------

with docs_tab:
    left, right = st.columns(2)

    with left:
        st.subheader("Sample company")
        st.write(
            "Documents from *Brightline Furnishings Ltd*, with a planted contradiction: the "
            "2025 account plan and the 2026 memo name different account managers for Harbor "
            "Hotels Group."
        )
        sample_docs = list(FolderConnector({"path": SAMPLE_DIR}, "samples").read())
        for doc in sample_docs:
            st.write(f"- `{doc.file_name}`")
        if st.button("Load the sample documents", type="primary", disabled=not key_ok):
            ingest(
                [
                    (d, next((f for k, f in SAMPLE_FUNCTIONS.items() if k in d.id), "Sales"))
                    for d in sample_docs
                ]
            )

    with right:
        st.subheader("Upload your own")
        uploads = st.file_uploader(
            "Documents",
            type=[s.lstrip(".") for s in SUPPORTED_SUFFIXES],
            accept_multiple_files=True,
        )
        function = st.selectbox("Business function", functions)
        day = st.date_input("Document date (if the file has none of its own)", date.today())
        if uploads:
            documents = [
                Document(
                    id=upload.name,
                    source="upload:ui",
                    title=Path(upload.name).stem.replace("_", " "),
                    file_name=upload.name,
                    content=upload.getvalue(),
                    modified_at=datetime.combine(day, time(), tzinfo=UTC),
                )
                for upload in uploads
            ]
            with st.expander("Preview how Docling splits the files (no LLM, free)"):
                for doc in documents:
                    parsed = preview(doc, doc.content_hash)
                    st.markdown(f"**{doc.file_name}**: {parsed.title}")
                    for s in parsed.sections:
                        st.markdown(f"- *{s.heading}* {s.pages}: {s.text[:150]}…")
            if st.button("Ingest uploaded files", disabled=not key_ok):
                ingest([(doc, function) for doc in documents])

    st.subheader("Documents in the graph")
    if key_ok:
        try:
            stored = run_with_graph(lambda g: g.list_documents())
        except Exception as exc:
            st.error(f"Could not reach Neo4j. Is Docker running? ({type(exc).__name__}: {exc})")
            stored = []
        if stored:
            st.dataframe(
                [
                    {
                        "document": d.file or d.title,
                        "date": d.observed_at.date(),
                        "business function": d.business_function,
                        "source": d.source,
                        "sections": d.sections,
                    }
                    for d in stored
                ],
                hide_index=True,
                use_container_width=True,
            )
        else:
            st.info("The graph is empty. Load the sample documents or upload your own.")

# --- Tab 2: ask --------------------------------------------------------------------------------

with ask_tab:
    question = st.text_input("Question", EXAMPLE_QUESTION)
    if st.button("Ask", type="primary", disabled=not key_ok) and question.strip():
        with st.spinner("Searching the graph…"):
            try:
                st.session_state["answer"] = run_with_graph(
                    lambda g: answer_question(g, question.strip())
                )
            except Exception as exc:
                st.exception(exc)

    answer: Answer | None = st.session_state.get("answer")
    if answer:
        st.markdown(f"#### Answer\n{answer.text}")
        conflicts = [f for f in answer.facts if f.status in (Status.SUPERSEDED, Status.DISPUTED)]
        if conflicts:
            st.warning(
                f"{len(conflicts)} contradiction(s) found between sources. "
                "Superseded facts lost to a newer source; disputed facts could not be resolved."
            )
        st.markdown("#### Facts and sources")
        only_cited = st.toggle("Show only facts cited in the answer", value=True)
        rows = []
        for n, fact in enumerate(answer.facts, start=1):
            if only_cited and n not in answer.cited:
                continue
            refs = [answer.sources[u] for u in fact.sources]
            rows.append(
                {
                    "#": n,
                    "status": STATUS_BADGES[fact.status],
                    "fact": fact.text,
                    "relation": fact.relation.upper(),
                    "since": fact.observed_at.date() if fact.observed_at else None,
                    "ended": fact.ended_at.date() if fact.ended_at else None,
                    "source": "; ".join(sorted({r.location for r in refs})),
                    "function": ", ".join(sorted({r.business_function for r in refs})),
                    "note": fact.note,
                }
            )
        st.dataframe(rows, hide_index=True, use_container_width=True)
