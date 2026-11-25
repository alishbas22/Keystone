"""Home: ask a question and see the answer with its sources."""

import streamlit as st
from common import STATUS_BADGES, require_setup, run_with_graph

from keystone.graph.qa import Answer, answer_question
from keystone.graph.truth import Status

EXAMPLE_QUESTION = "Who is our biggest customer and who manages their account?"

st.title("🗝️ Keystone")
st.caption("Every fact has a source. Every answer has proof.")
ready = require_setup()

question = st.text_input("Ask a question about the company", EXAMPLE_QUESTION)
if st.button("Ask", type="primary", disabled=not ready) and question.strip():
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
            f"{len(conflicts)} contradiction(s) between sources. Superseded facts lost to a newer "
            "or more trusted source; disputed facts need review on the Conflicts page."
        )
    st.markdown("#### Facts and sources")
    only_cited = st.toggle("Only facts cited in the answer", value=True)
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
                "since": fact.observed_at.date() if fact.observed_at else None,
                "ended": fact.ended_at.date() if fact.ended_at else None,
                "source": "; ".join(sorted({r.location for r in refs})),
                "function": ", ".join(sorted({r.business_function for r in refs})),
                "note": fact.note,
            }
        )
    st.dataframe(rows, hide_index=True, use_container_width=True)
