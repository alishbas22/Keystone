"""Documents page (KAN-31): upload, parsing status, delete and re-ingest."""

from datetime import UTC, date, datetime, time
from pathlib import Path

import streamlit as st
from common import require_setup, run_with_graph, settings

from keystone.connectors import Document
from keystone.connectors.folder import FolderConnector
from keystone.graph.engine import KeystoneGraph, StoredDocument
from keystone.ingest.parse import SUPPORTED_SUFFIXES, ParsedDocument, parse_document
from keystone.ingest.pipeline import IngestResult, ingest_document, reingest, save_upload
from keystone.ontology import active_ontology

SAMPLE_DIR = Path(__file__).resolve().parents[2] / "demo" / "data"
# The small demo: company overview + the account manager contradiction.
CORE_SAMPLES = {
    "2025-01-15_company_overview.pdf",
    "2025-03-10_harbor_hotels_account_plan.docx",
    "2025-06-30_q2_customer_report.xlsx",
    "2025-09-02_q3_finance_review.pptx",
    "2026-09-15_account_reassignment_memo.docx",
}

st.title("📄 Documents")
st.caption("Upload documents, check how they were read, and remove or re-read them.")
ready = require_setup()


@st.cache_data(show_spinner=False, max_entries=50)
def preview(_document: Document, content_hash: str) -> ParsedDocument:
    return parse_document(_document)


def show_results(results: list[IngestResult]) -> None:
    icons = {"ingested": "✅", "skipped": "⏭️", "failed": "❌"}
    for r in results:
        detail = f"{r.sections} sections" if r.status == "ingested" else r.message
        st.write(f"{icons.get(r.status, '•')} **{r.title}**: {r.status} ({detail})")


def ingest(items: list[tuple[Document, str | None]]) -> None:
    status = st.status(
        "Reading and extracting facts… (the free Gemini tier is slow)", expanded=True
    )

    async def work(graph: KeystoneGraph) -> list[IngestResult]:
        results = []
        for document, function in items:
            status.write(f"📄 {document.title}")
            results.append(await ingest_document(graph, document, function, status.caption))
        return results

    try:
        st.session_state["last_results"] = run_with_graph(work)
        status.update(label="Done", state="complete", expanded=False)
    except Exception as exc:
        status.update(label="Ingestion stopped", state="error")
        st.exception(exc)


upload_tab, sample_tab = st.tabs(["Upload files", "Sample company"])

with upload_tab:
    functions = active_ontology().business_functions
    uploads = st.file_uploader(
        "Documents (" + ", ".join(SUPPORTED_SUFFIXES) + ")",
        type=[s.lstrip(".") for s in SUPPORTED_SUFFIXES],
        accept_multiple_files=True,
    )
    left, right = st.columns(2)
    function = left.selectbox(
        "Business function", functions, help="Only agents with this function will see the facts."
    )
    day = right.date_input(
        "Document date", date.today(), help="Used when the file has no date of its own (emails do)."
    )
    if uploads:
        written = datetime.combine(day, time(), tzinfo=UTC)
        documents = [
            Document(
                id=u.name,
                source="upload:ui",
                title=Path(u.name).stem.replace("_", " "),
                file_name=u.name,
                content=u.getvalue(),
                modified_at=written,
            )  # fmt: skip
            for u in uploads
        ]
        with st.expander("Preview: how the files will be read (free, no AI)"):
            for doc in documents:
                try:
                    parsed = preview(doc, doc.content_hash)
                except Exception as exc:
                    st.error(f"{doc.file_name}: {exc}")
                    continue
                st.markdown(
                    f"**{doc.file_name}** → *{parsed.title}*, {len(parsed.sections)} sections"
                )
                for s in parsed.sections:
                    st.markdown(f"- **{s.heading}** {s.pages}: {s.text[:160]}…")
        if st.button(f"Ingest {len(uploads)} file(s)", type="primary", disabled=not ready):
            upload_dir = settings().upload_dir
            saved = [
                save_upload(upload_dir, d.file_name or d.id, d.content or b"", written)
                for d in documents
            ]
            ingest([(d, function) for d in saved])

with sample_tab:
    st.write(
        "30 documents from *Brightline Furnishings Ltd* (PDF, Word, Excel, PowerPoint, email, "
        "Markdown, CSV) filed in Sales, Finance, HR and Operations folders, with five planted "
        "contradictions (see `demo/ground_truth.yaml`)."
    )
    samples = list(FolderConnector({"path": SAMPLE_DIR}, "samples").read())
    which = st.radio(
        "Load",
        [f"Core demo ({len(CORE_SAMPLES)} documents, the account manager contradiction)",
         f"All {len(samples)} documents (hours on the free Gemini tier)"],
    )  # fmt: skip
    chosen_samples = (
        [d for d in samples if d.file_name in CORE_SAMPLES] if which.startswith("Core") else samples
    )
    st.dataframe(
        [{"file": d.id, "business function": d.business_function} for d in chosen_samples],
        hide_index=True,
    )
    if st.button("Load the sample company", disabled=not ready):
        ingest([(d, None) for d in chosen_samples])  # business function from the folder

if st.session_state.get("last_results"):
    st.subheader("Last ingestion")
    show_results(st.session_state["last_results"])

st.subheader("Documents in the graph")
if ready:
    try:
        stored: list[StoredDocument] = run_with_graph(lambda g: g.list_documents())
    except Exception as exc:
        st.error(f"Could not reach Neo4j. Is Docker running? ({type(exc).__name__}: {exc})")
        stored = []
    if not stored:
        st.info("No documents yet. Upload some or load the sample company.")
    else:
        table = st.dataframe(
            [
                {
                    "document": d.file or d.title,
                    "title": d.title,
                    "date": d.observed_at.date(),
                    "business function": d.business_function,
                    "sections": d.sections,
                    "source": d.source,
                    "author": d.author or "",
                }
                for d in stored
            ],
            hide_index=True,
            use_container_width=True,
            on_select="rerun",
            selection_mode="multi-row",
            key="stored_docs",
        )
        chosen = [stored[i] for i in table.selection.rows]  # type: ignore[attr-defined]
        st.caption(f"{len(stored)} documents · select rows to delete or re-read them")
        if chosen:
            names = ", ".join(d.file or d.title for d in chosen)
            col1, col2 = st.columns(2)
            with col1:
                confirm = st.checkbox(
                    f"Delete {len(chosen)} document(s) and the facts only they support"
                )
                if st.button("🗑️ Delete", disabled=not confirm):

                    async def delete(graph: KeystoneGraph) -> None:
                        for d in chosen:
                            await graph.delete_document(d.doc_id)

                    run_with_graph(delete)
                    st.success(f"Deleted: {names}")
                    st.rerun()
            with col2:
                if st.button("🔄 Re-ingest", help="Delete and read the original file again"):

                    async def redo(graph: KeystoneGraph) -> list[IngestResult]:
                        return [await reingest(graph, d) for d in chosen]

                    with st.spinner("Re-reading…"):
                        st.session_state["last_results"] = run_with_graph(redo)
                    st.rerun()
