"""Keystone demo UI: upload documents, ask a question, see the answer with its sources.

uv run streamlit run ui/app.py
"""

import asyncio
import tempfile
from collections.abc import Awaitable, Callable
from datetime import UTC, date, datetime, time
from pathlib import Path
from typing import TypeVar

import streamlit as st

from keystone.net import configure_network

configure_network()

from graphiti_core import Graphiti  # noqa: E402

from keystone.config import get_settings  # noqa: E402
from keystone.graph.engine import (  # noqa: E402
    MissingApiKeyError,
    delete_all,
    ingest_document,
    list_documents,
    open_graph,
)
from keystone.graph.qa import Answer, answer_question  # noqa: E402
from keystone.graph.truth import Status  # noqa: E402
from keystone.ingest.parse import (  # noqa: E402
    SUPPORTED_SUFFIXES,
    ParsedDocument,
    date_from_filename,
    parse_file,
)
from keystone.ontology import core_ontology  # noqa: E402

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
functions = core_ontology().business_functions

st.set_page_config(page_title="Keystone demo", page_icon="🗝️", layout="wide")


def run_with_graph(action: Callable[[Graphiti], Awaitable[T]]) -> T:
    """Run an async graph action from Streamlit's synchronous script."""

    async def main() -> T:
        async with open_graph(settings) as graph:
            if not st.session_state.get("indices_ready"):
                await graph.build_indices_and_constraints()
                st.session_state["indices_ready"] = True
            return await action(graph)

    return asyncio.run(main())


@st.cache_data(show_spinner=False)
def parse_cached(path: str, _mtime: float) -> ParsedDocument:
    return parse_file(Path(path))


def ingest(items: list[tuple[Path, str, datetime]]) -> None:
    status = st.status("Ingesting documents…", expanded=True)

    async def work(graph: Graphiti) -> None:
        for path, function, observed in items:
            status.write(f"📄 **{path.name}**: reading with Docling…")
            doc = parse_cached(str(path), path.stat().st_mtime)
            bar = status.progress(0.0, text=f"{len(doc.sections)} sections")

            def progress(i: int, total: int, heading: str, bar=bar) -> None:  # noqa: B008
                bar.progress((i - 1) / total, text=f"Extracting section {i}/{total}: {heading}")

            stored = await ingest_document(graph, doc, function, observed, progress, settings)
            bar.progress(1.0, text="Done" if stored else "Already in the graph, skipped")

    try:
        run_with_graph(work)
        status.update(label="Ingestion finished", state="complete")
    except Exception as exc:
        status.update(label="Ingestion failed", state="error")
        st.exception(exc)


# --- Sidebar: setup checks ---------------------------------------------------------------------

with st.sidebar:
    st.header("Setup")
    key_ok = bool(settings.openai_api_key.get_secret_value())
    if key_ok:
        st.success("OpenAI API key found")
    else:
        st.error(
            "No OpenAI API key. Add `OPENAI_API_KEY=sk-...` to the `.env` file in the "
            "keystone folder, then restart this app."
        )
    st.caption(f"Neo4j: `{settings.neo4j_uri}` · graph: `{settings.graph_group}`")
    st.divider()
    with st.expander("Danger zone"):
        confirm = st.checkbox("Yes, delete everything in the demo graph")
        if st.button("Delete demo data", disabled=not (confirm and key_ok)):
            run_with_graph(lambda g: delete_all(g, settings.graph_group))
            st.success("Demo graph cleared.")

st.title("🗝️ Keystone")
st.caption("Every fact has a source. Every answer has proof.")

docs_tab, ask_tab = st.tabs(["1 · Documents", "2 · Ask a question"])

# --- Tab 1: documents --------------------------------------------------------------------------

with docs_tab:
    left, right = st.columns(2)

    with left:
        st.subheader("Sample company")
        samples = sorted(SAMPLE_DIR.glob("*.*")) if SAMPLE_DIR.exists() else []
        samples = [p for p in samples if p.suffix.lower() in SUPPORTED_SUFFIXES]
        st.write(
            "Five documents from *Brightline Furnishings Ltd*. One planted contradiction: "
            "the 2025 account plan and the 2026 memo name different account managers for "
            "Harbor Hotels Group."
        )
        for p in samples:
            st.write(f"- `{p.name}`")
        if st.button("Load the sample documents", type="primary", disabled=not key_ok):
            items = []
            for p in samples:
                function = next((f for k, f in SAMPLE_FUNCTIONS.items() if k in p.name), "Sales")
                items.append((p, function, date_from_filename(p.name) or datetime.now(UTC)))
            ingest(items)

    with right:
        st.subheader("Upload your own")
        uploads = st.file_uploader(
            "Documents",
            type=[s.lstrip(".") for s in SUPPORTED_SUFFIXES],
            accept_multiple_files=True,
        )
        function = st.selectbox("Business function", functions)
        fallback_day = st.date_input(
            "Document date (used when the file name does not start with YYYY-MM-DD)", date.today()
        )
        if uploads:
            upload_dir = Path(tempfile.gettempdir()) / "keystone_uploads"
            upload_dir.mkdir(exist_ok=True)
            paths = []
            for upload in uploads:
                target = upload_dir / Path(upload.name).name
                target.write_bytes(upload.getvalue())
                paths.append(target)
            with st.expander("Preview how Docling splits the files (no LLM, free)"):
                for p in paths:
                    doc = parse_cached(str(p), p.stat().st_mtime)
                    st.markdown(f"**{p.name}**: {doc.title}")
                    for s in doc.sections:
                        st.markdown(f"- *{s.heading}* {s.pages}: {s.text[:150]}…")
            if st.button("Ingest uploaded files", disabled=not key_ok):
                fallback = datetime.combine(fallback_day, time(), tzinfo=UTC)
                ingest([(p, function, date_from_filename(p.name) or fallback) for p in paths])

    st.subheader("Documents in the graph")
    if key_ok:
        try:
            docs = run_with_graph(lambda g: list_documents(g, settings.graph_group))
        except MissingApiKeyError as exc:
            st.error(str(exc))
            docs = []
        except Exception as exc:
            st.error(f"Could not reach Neo4j. Is Docker running? ({type(exc).__name__}: {exc})")
            docs = []
        if docs:
            st.dataframe(
                [
                    {
                        "file": d.file,
                        "date": d.observed_at.date() if d.observed_at else None,
                        "business function": d.business_function,
                        "sections": d.sections,
                    }
                    for d in docs
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
            refs = [answer.sources[u].ref for u in fact.sources]
            rows.append(
                {
                    "#": n,
                    "status": STATUS_BADGES[fact.status],
                    "fact": fact.text,
                    "relation": fact.relation.upper(),
                    "since": fact.observed_at.date() if fact.observed_at else None,
                    "ended": fact.ended_at.date() if fact.ended_at else None,
                    "source": "; ".join(f"{r.file} › {r.section} {r.pages}".strip() for r in refs),
                    "function": ", ".join(sorted({r.business_function for r in refs})),
                    "note": fact.note,
                }
            )
        st.dataframe(rows, hide_index=True, use_container_width=True)
