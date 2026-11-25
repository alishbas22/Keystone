"""Conflict review (KAN-33): contradictions between sources, and a person's decision."""

import streamlit as st
from common import STATUS_BADGES, require_setup, run_with_graph

from keystone.graph import queries
from keystone.graph.facts import clear_decision, record_decision
from keystone.graph.queries import ConflictView, FactView
from keystone.ontology import active_ontology

st.title("⚖️ Conflicts")
st.caption(
    "Contradictions between documents. Rules decide automatically (newer source, more "
    "authoritative source, more documents); a reviewer's choice overrides them."
)
if not require_setup():
    st.stop()

functions = active_ontology().business_functions
left, right = st.columns([2, 1])
scope = left.multiselect("Business functions", functions, default=functions)
reviewer = right.text_input("Your name (recorded with decisions)", key="reviewer")
only_open = st.toggle("Only open conflicts (still disputed)", value=False)

conflicts: list[ConflictView] = run_with_graph(
    lambda g: queries.get_conflicts(g, scope, include_resolved=not only_open)
)

open_count = sum(1 for c in conflicts if c.open)
m1, m2, m3 = st.columns(3)
m1.metric("Conflicts", len(conflicts))
m2.metric("Open (need review)", open_count)
m3.metric("Decided by a person", sum(1 for c in conflicts if c.decided_by == "person"))

if not conflicts:
    st.success("No contradictions found.")
    st.stop()


def show_fact(
    column: st.delta_generator.DeltaGenerator, label: str, fact: FactView, won: bool
) -> None:
    with column.container(border=True):
        st.markdown(f"**{label}** {'🏆' if won else ''}")
        st.markdown(f"{fact.text}")
        st.caption(
            f"{STATUS_BADGES.get(fact.status, fact.status)} · since {fact.since:%Y-%m-%d}"
            if fact.since
            else STATUS_BADGES.get(fact.status, fact.status)
        )
        for s in fact.sources:
            st.caption(
                f"📎 {s.location} · {s.source_type} · {s.observed_at:%Y-%m-%d} · "
                f"{s.author or 'unknown author'} · {s.business_function}"
            )


for i, conflict in enumerate(conflicts):
    if len(conflict.facts) < 2:
        continue
    a, b = conflict.facts[0], conflict.facts[1]
    state = "🔴 open" if conflict.open else f"✅ decided by {conflict.decided_by}"
    with st.expander(
        f"{state}: {a.subject} / {b.subject} · {conflict.reason}", expanded=conflict.open
    ):
        st.write(f"**Why they conflict:** {conflict.reason}")
        st.write(f"**Outcome:** {conflict.explanation}")
        col_a, col_b = st.columns(2)
        show_fact(col_a, "A", a, conflict.winner == a.id)
        show_fact(col_b, "B", b, conflict.winner == b.id)

        k1, k2, k3 = st.columns(3)
        who = reviewer.strip() or "reviewer"
        if k1.button(
            "Keep A",
            key=f"a-{i}",
            disabled=conflict.winner == a.id and conflict.decided_by == "person",
        ):
            run_with_graph(lambda g, w=a.id, lo=b.id: record_decision(g, w, lo, by=f"person:{who}"))
            st.rerun()
        if k2.button(
            "Keep B",
            key=f"b-{i}",
            disabled=conflict.winner == b.id and conflict.decided_by == "person",
        ):
            run_with_graph(lambda g, w=b.id, lo=a.id: record_decision(g, w, lo, by=f"person:{who}"))
            st.rerun()
        if conflict.decided_by == "person" and k3.button("Undo (use rules)", key=f"u-{i}"):
            run_with_graph(lambda g, x=a.id, y=b.id: clear_decision(g, x, y))
            st.rerun()
