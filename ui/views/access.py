"""Sources and agent access (KAN-34): connect sources, manage agent keys, audit log, coverage."""

from pathlib import Path

import streamlit as st
from common import require_setup, run_with_graph, settings

from keystone.connectors import BaseConnector, create_connector
from keystone.connectors.sql import SqlConnector
from keystone.connectors.sql_mapping import validate_mapping
from keystone.graph.engine import KeystoneGraph
from keystone.graph.scopes import function_summary, untagged
from keystone.ingest.pipeline import SyncReport, sync_connector
from keystone.mcp_server.audit import read_log
from keystone.mcp_server.auth import AgentRegistry
from keystone.ontology import active_ontology

st.title("⚙️ Sources & agents")
st.caption(
    "Connect data sources, give agents keys for their business functions, and review access."
)
if not require_setup():
    st.stop()

functions = active_ontology().business_functions


async def _coverage(graph: KeystoneGraph) -> tuple[dict[str, dict[str, int]], dict[str, list[str]]]:
    return await function_summary(graph), await untagged(graph)


sources_tab, agents_tab, audit_tab, coverage_tab = st.tabs(
    ["Sources", "Agents", "Audit log", "Coverage"]
)

# --- Sources -------------------------------------------------------------------------------------


def sync(connector: BaseConnector, function: str | None) -> None:  # type: ignore[type-arg]
    status = st.status("Syncing… (the free Gemini tier is slow)", expanded=True)

    async def work(graph: KeystoneGraph) -> SyncReport:
        return await sync_connector(
            graph, connector, business_function=function, progress=status.caption
        )

    try:
        report = run_with_graph(work)
        status.update(label=f"Sync finished: {report.summary()}", state="complete")
        for r in report.results:
            if r.status == "failed":
                st.warning(f"{r.title}: {r.message}")
    except Exception as exc:
        status.update(label="Sync stopped", state="error")
        st.exception(exc)


with sources_tab:
    kind = st.radio(
        "Source type", ["Folder", "Database (PostgreSQL, MySQL, SQLite…)"], horizontal=True
    )
    if kind == "Folder":
        path = st.text_input("Folder path", placeholder=r"C:\Shared\Sales")
        default_function = st.selectbox(
            "Business function (subfolders named Sales/Finance/HR/Operations override it)",
            functions,
        )
        if path:
            folder = create_connector(
                "folder", {"path": path, "business_function": default_function}, "ui"
            )
            result = folder.check()
            (st.success if result.ok else st.error)(result.message)
            if result.ok:
                st.dataframe(
                    [{"stream": s.name, "documents": s.document_count} for s in folder.discover()],
                    hide_index=True,
                )
                if st.button("Sync folder", type="primary"):
                    sync(folder, None)
    else:
        url = st.text_input(
            "Database URL (used for this session only, never stored)",
            type="password",
            placeholder="postgresql+psycopg://user:password@host:5432/database",
        )
        owner = st.text_input("Your company's name (for 'customer of' links)")
        mapping_path = Path(st.text_input("Mapping file", "data/mappings/database.yaml"))
        if url:
            connector = SqlConnector(
                {"url": url, "owner_organization": owner or None, "mapping_file": mapping_path},
                "ui",
            )
            try:
                tables = connector.describe_schema()
            except Exception as exc:
                st.error(f"Cannot connect: {type(exc).__name__}: {exc}")
                st.stop()
            mapping = connector.mapping()
            st.write(f"**{len(tables)} tables.** Proposed mapping onto the ontology:")
            st.dataframe(
                [
                    {
                        "table": name,
                        "becomes": tm.entity_type
                        or (tm.junction.relation if tm.junction else "skipped"),
                        "kind": tm.kind,
                        "business function": tm.business_function or "",
                        "note": tm.note,
                    }
                    for name, tm in mapping.tables.items()
                ],
                hide_index=True,
                use_container_width=True,
            )
            problems = validate_mapping(mapping, connector.ontology, tables)
            for problem in problems:
                st.error(problem)
            edited = st.text_area(
                "Review and edit the mapping (YAML)", mapping.to_yaml(), height=300
            )
            c1, c2 = st.columns(2)
            if c1.button("Save mapping"):
                mapping_path.parent.mkdir(parents=True, exist_ok=True)
                mapping_path.write_text(edited, encoding="utf-8")
                st.success(f"Saved {mapping_path}. Set `reviewed: true` once checked.")
                st.rerun()
            if c2.button("Sync database", type="primary", disabled=bool(problems)):
                if not mapping.reviewed:
                    st.warning(
                        "Syncing with an unreviewed mapping. Review it and set `reviewed: true`."
                    )
                sync(connector, None)
        else:
            st.caption(
                "Tip: mappings can also be proposed from the command line (docs/connectors.md)."
            )

# --- Agents --------------------------------------------------------------------------------------

with agents_tab:
    st.write(
        "Each agent gets a secret key tied to business functions. It sees and writes only those "
        "functions' data. Only a fingerprint of the key is stored."
    )
    with st.form("new_agent", clear_on_submit=True):
        name = st.text_input("Agent name", placeholder="sales-bot")
        chosen = st.multiselect("Business functions", functions)
        if st.form_submit_button("Create agent key", type="primary"):
            try:
                agent, key = run_with_graph(lambda g: AgentRegistry(g).create(name.strip(), chosen))
                st.session_state["new_key"] = (agent.name, key)
            except ValueError as exc:
                st.error(str(exc))
    if st.session_state.get("new_key"):
        agent_name, key = st.session_state["new_key"]
        st.success(f"Key for **{agent_name}**. Copy it now: it will not be shown again.")
        st.code(key, language=None)
        if st.button("I have copied it"):
            del st.session_state["new_key"]
            st.rerun()

    records = run_with_graph(lambda g: AgentRegistry(g).list_agents())
    if not records:
        st.info("No agents yet.")
    for record in records:
        with st.container(border=True):
            c1, c2, c3 = st.columns([2, 3, 1])
            state = "🟢 active" if record.active else "⛔ revoked"
            c1.markdown(f"**{record.name}**  \n{state} · key `{record.key_hint}…`")
            new_functions = c2.multiselect(
                "Business functions", functions, default=record.functions, key=f"f-{record.name}",
                disabled=not record.active, label_visibility="collapsed",
            )  # fmt: skip
            if record.active and new_functions != record.functions and new_functions:
                run_with_graph(
                    lambda g, n=record.name, f=new_functions: AgentRegistry(g).set_functions(n, f)
                )
                st.rerun()
            if record.active and c3.button("Revoke", key=f"r-{record.name}"):
                run_with_graph(lambda g, n=record.name: AgentRegistry(g).revoke(n))
                st.rerun()
    st.caption(
        "Agents connect with `uv run python -m keystone.mcp_server --http` and send "
        "`Authorization: Bearer <key>`, or run the stdio server with KEYSTONE_AGENT_KEY set."
    )

# --- Audit log -----------------------------------------------------------------------------------

with audit_tab:
    agent_filter = st.selectbox("Agent", ["All"] + [r.name for r in records] + ["unknown"])
    entries = run_with_graph(
        lambda g: read_log(g, None if agent_filter == "All" else agent_filter, limit=200)
    )
    if not entries:
        st.info("No agent activity yet.")
    else:
        st.dataframe(
            [
                {
                    "time": e.at.strftime("%Y-%m-%d %H:%M:%S"),
                    "agent": e.agent,
                    "tool": e.tool,
                    "outcome": {"ok": "✅ ok", "denied": "⛔ denied", "error": "❌ error"}.get(
                        e.outcome, e.outcome
                    ),
                    "scope": ", ".join(e.scope),
                    "returned": len(e.returned_ids),
                    "arguments": ", ".join(
                        f"{k}={v}" for k, v in e.arguments.items() if v not in (None, "")
                    ),
                    "message": e.message,
                }
                for e in entries
            ],
            hide_index=True,
            use_container_width=True,
        )

# --- Coverage ------------------------------------------------------------------------------------

with coverage_tab:
    summary, missing = run_with_graph(lambda g: _coverage(g))
    st.write("What each business function can see:")
    cols = st.columns(len(summary))
    for col, (function, counts) in zip(cols, summary.items(), strict=True):
        col.metric(
            function,
            f"{counts['entities']} entities",
            f"{counts['facts']} facts",
            delta_color="off",
        )
    if missing["nodes"] or missing["edges"]:
        st.warning(
            f"{len(missing['nodes'])} entities and {len(missing['edges'])} facts have no business "
            "function (no provenance). They are invisible to every agent."
        )
    else:
        st.success("Every entity and fact is tagged with a business function.")
    st.caption(f"Graph `{settings().graph_group}`")
