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


def test_home_page_loads(monkeypatch: pytest.MonkeyPatch) -> None:
    with seeded_ui(monkeypatch):
        app = run_page("home.py")
        no_errors(app)
        assert app.text_input[0].value.startswith("Who is our biggest customer")
