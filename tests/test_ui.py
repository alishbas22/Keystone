"""KAN-31 to KAN-34: Streamlit pages render against a seeded graph (headless, no LLM calls).

These tests are synchronous on purpose: each page runs its own event loop via asyncio.run.
"""

import pytest

from tests.ui_support import run_page, seeded_ui


def no_errors(app: object) -> None:
    exceptions = [e.value for e in app.exception]  # type: ignore[attr-defined]
    errors = [e.value for e in app.error]  # type: ignore[attr-defined]
    assert exceptions == [] and errors == [], (exceptions, errors)


def test_documents_page_lists_stored_documents(monkeypatch: pytest.MonkeyPatch) -> None:
    with seeded_ui(monkeypatch):
        app = run_page("documents.py")
        no_errors(app)
        stored = app.dataframe[-1].value
        assert sorted(stored["document"]) == [
            "invoice.docx",
            "memo.docx",
            "plan.docx",
            "review.docx",
        ]
        assert set(stored["business function"]) == {"Sales", "Finance", "HR"}
        assert any(t.label == "Upload files" for t in app.tabs)


def test_explorer_shows_profile_and_respects_view_as(monkeypatch: pytest.MonkeyPatch) -> None:
    with seeded_ui(monkeypatch):
        app = run_page("explorer.py")
        no_errors(app)
        app.text_input[0].set_value("Sara").run()
        no_errors(app)
        assert app.header[0].value == "Sara Khan"
        assert [m.value for m in app.metric][:1] == ["Person"]
        timeline = app.dataframe[0].value
        assert set(timeline["fact"]) == {
            "Sara Khan manages account Harbor Hotels Group",
            "Sara Khan reports to Omar Siddiqui",
        }
        # As a Sales-only agent, the HR reporting line disappears.
        app.multiselect[0].set_value(["Sales"]).run()
        assert set(app.dataframe[0].value["fact"]) == {
            "Sara Khan manages account Harbor Hotels Group"
        }


def test_conflict_review_and_decision(monkeypatch: pytest.MonkeyPatch) -> None:
    with seeded_ui(monkeypatch):
        app = run_page("conflicts.py")
        no_errors(app)
        assert [m.value for m in app.metric] == ["1", "0", "0"]
        conflict = app.expander[0]
        assert "decided by time" in conflict.label
        # A reviewer overrides the time rule: keep the older claim (A = Sara, from 2025).
        app.text_input(key="reviewer").set_value("Alishba").run()
        keep_a = next(b for b in app.button if b.label == "Keep A")
        keep_a.click().run()
        no_errors(app)
        assert [m.value for m in app.metric] == ["1", "0", "1"]
        assert "decided by person" in app.expander[0].label
        next(b for b in app.button if b.label.startswith("Undo")).click().run()
        assert "decided by time" in app.expander[0].label


def test_access_page_agents_audit_and_coverage(monkeypatch: pytest.MonkeyPatch) -> None:
    with seeded_ui(monkeypatch):
        app = run_page("access.py")
        no_errors(app)
        # Coverage: every fixture entity and fact is tagged with a business function.
        assert any("Every entity and fact is tagged" in s.value for s in app.success)
        coverage = {m.label: m.value for m in app.metric}
        assert coverage["Sales"] == "4 entities" and coverage["HR"] == "2 entities"

        # Create an agent: the key is shown once.
        name_input = next(t for t in app.text_input if t.label == "Agent name")
        name_input.set_value("sales-bot")
        next(m for m in app.multiselect if m.label == "Business functions").set_value(["Sales"])
        next(b for b in app.button if b.label == "Create agent key").click().run()
        no_errors(app)
        assert app.code[0].value.startswith("ks_")
        assert any("**sales-bot**" in md.value for md in app.markdown)


def test_home_page_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    with seeded_ui(monkeypatch):
        app = run_page("home.py")
        no_errors(app)
        assert app.text_input[0].value.startswith("Who is our biggest customer")
