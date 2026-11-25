"""Graph explorer (KAN-32): entities, relationships, facts over time and their sources."""

import streamlit as st
from common import STATUS_BADGES, require_setup, run_with_graph

from keystone.graph import queries
from keystone.graph.engine import KeystoneGraph
from keystone.graph.queries import EntityProfile, FactView
from keystone.graph.resolution import (
    MergeCandidate,
    find_candidates,
    load_entities,
    merge,
    same_as,
    unmerge,
)
from keystone.ontology import active_ontology

st.title("🕸️ Graph explorer")
st.caption("Find an entity, see how it is connected, and trace every fact to its source.")
if not require_setup():
    st.stop()

functions = active_ontology().business_functions
view_as = st.multiselect(
    "View as business function(s)",
    functions,
    default=functions,
    help="See exactly what an agent with these business functions would see.",
)
query = st.text_input("Search entities", placeholder="e.g. Harbor, Sara, INV-2025")

if not query.strip():
    st.info("Type a name to search. Try 'Harbor' with the sample company loaded.")
    st.stop()

results = run_with_graph(lambda g: queries.search_entities(g, query, view_as, 20))
if not results:
    st.warning("Nothing found (or nothing visible to the selected business functions).")
    st.stop()

labels = {f"{r.name}  ·  {r.entity_type}": r for r in results}
choice = st.radio("Results", list(labels), label_visibility="collapsed")
selected = labels[choice]


async def load(
    graph: KeystoneGraph,
) -> tuple[EntityProfile | None, list[MergeCandidate], list[str]]:
    profile = await queries.get_entity(graph, selected.uuid, view_as)
    entities = await load_entities(graph)
    identity = {n: e.identity_properties for n, e in graph.ontology.entity_types.items()}
    candidates = [
        c for c in find_candidates(entities, identity) if selected.uuid in (c.a.uuid, c.b.uuid)
    ]
    return profile, candidates, await same_as(graph, selected.uuid)


profile, candidates, cluster = run_with_graph(load)
if profile is None:
    st.warning("Not visible to the selected business functions.")
    st.stop()

entity = profile.entity
st.header(f"{entity.name}")
c1, c2, c3 = st.columns(3)
c1.metric("Type", entity.entity_type)
c2.metric("Current facts", len(profile.current_facts))
c3.metric("Past facts", len(profile.past_facts))
st.caption(
    f"Business functions: {', '.join(profile.business_functions) or '–'}"
    + (f" · Also known as: {', '.join(entity.aliases)}" if entity.aliases else "")
)
if entity.summary:
    st.write(entity.summary)
if profile.properties:
    st.json(profile.properties, expanded=False)

facts: list[FactView] = profile.current_facts + profile.past_facts


def dot(facts: list[FactView], centre: str) -> str:
    """Graphviz DOT for the entity's neighbourhood (current facts solid, past dashed)."""

    def q(text: str) -> str:
        return '"' + text.replace('"', "'") + '"'

    lines = [
        "digraph G {",
        "rankdir=LR;",
        'node [shape=box, style="rounded,filled", fillcolor="#eef"];',
    ]
    lines.append(f'{q(centre)} [fillcolor="#ffd966"];')
    for f in facts:
        style = "solid" if f.status == "current" else "dashed"
        colour = {"current": "#2e7d32", "superseded": "#ef6c00", "disputed": "#c62828"}.get(
            f.status, "#9e9e9e"
        )
        lines.append(
            f"{q(f.subject)} -> {q(f.object)} "
            f'[label={q(f.relation.upper())}, style={style}, color="{colour}", fontsize=10];'
        )
    lines.append("}")
    return "\n".join(lines)


graph_tab, timeline_tab, duplicates_tab = st.tabs(
    ["Connections", "Facts over time", f"Possible duplicates ({len(candidates)})"]
)

with graph_tab:
    if facts:
        st.graphviz_chart(dot(facts, entity.name), use_container_width=True)
        st.caption("Solid green: current · dashed: history, superseded (orange) or disputed (red)")
    else:
        st.info("No facts visible for this entity.")

with timeline_tab:
    rows = sorted(facts, key=lambda f: (f.since is None, f.since))
    st.dataframe(
        [
            {
                "since": f.since.date() if f.since else None,
                "ended": f.ended.date() if f.ended else None,
                "status": STATUS_BADGES.get(f.status, f.status),
                "fact": f.text,
                "sources": "; ".join(s.location for s in f.sources),
                "functions": ", ".join(sorted({s.business_function for s in f.sources})),
                "note": f.note,
            }
            for f in rows
        ],
        hide_index=True,
        use_container_width=True,
    )
    with st.expander("Sources in detail"):
        for f in rows:
            st.markdown(f"**{f.text}** ({STATUS_BADGES.get(f.status, f.status)})")
            for s in f.sources:
                st.markdown(
                    f"- {s.location} · {s.source_type} · {s.observed_at:%Y-%m-%d} · "
                    f"author {s.author or 'unknown'} · confidence {s.confidence:.2f} · "
                    f"{s.business_function}"
                )

with duplicates_tab:
    merged = [u for u in cluster if u != entity.uuid]
    if merged:
        st.success(f"Merged with {len(merged)} other record(s): shown together everywhere.")
    if not candidates:
        st.info("No likely duplicates found for this entity.")
    for c in candidates:
        other = c.b if c.a.uuid == entity.uuid else c.a
        already = other.uuid in cluster
        col1, col2 = st.columns([3, 1])
        col1.write(f"**{other.name}** ({other.entity_type}) · score {c.score:.2f} · {c.reason}")
        if already:
            if col2.button("Unmerge", key=f"un-{other.uuid}"):
                run_with_graph(lambda g, o=other.uuid: unmerge(g, entity.uuid, o))
                st.rerun()
        elif col2.button("Merge", key=f"merge-{other.uuid}"):
            run_with_graph(
                lambda g, o=other.uuid, cand=c: merge(
                    g, entity.uuid, o, cand.reason, cand.score, by="reviewer:ui"
                )
            )
            st.rerun()
