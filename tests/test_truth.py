"""KAN-25: truth engine (time, source authority, corroboration, reviewer decisions)."""

from datetime import UTC, datetime

from keystone.graph.provenance import Provenance
from keystone.graph.truth import DecidedBy, Evidence, Fact, Status, classify, detect_conflicts
from keystone.ontology import core_ontology

RULES = core_ontology().rules


def fact(
    id: str,
    subject: str,
    relation: str,
    obj: str,
    when: tuple[int, int, int],
    sources: list[tuple[str, str]] | None = None,  # (doc_id, source_type)
    ended: datetime | None = None,
) -> Fact:
    observed = datetime(*when, tzinfo=UTC)
    evidence = tuple(
        Evidence(doc, kind, observed) for doc, kind in (sources or [(f"doc-{id}", "document")])
    )
    return Fact(
        id=id,
        subject=subject,
        relation=relation,
        object=obj,
        text=f"{subject} {relation} {obj}",
        observed_at=observed,
        ended_at=ended,
        evidence=evidence,
    )


def statuses(facts: list[Fact], **kwargs: object) -> dict[str, Status]:
    return {f.id: f.status for f in classify(facts, RULES, **kwargs)}  # type: ignore[arg-type]


SARA = ("Sara Khan", "manages_account", "Harbor Hotels Group")
BILAL = ("Bilal Ahmed", "manages_account", "Harbor Hotels Group")


# --- 1. Time -------------------------------------------------------------------------------------


def test_planted_contradiction_newer_account_manager_wins() -> None:
    """The demo dataset: 2025 plan says Sara, 2026 memo says Bilal manages Harbor Hotels."""
    old = fact("a", *SARA, (2025, 3, 10))
    new = fact("b", *BILAL, (2026, 9, 1))
    result = {f.id: f for f in classify([old, new], RULES)}
    assert (result["a"].status, result["b"].status) == (Status.SUPERSEDED, Status.CURRENT)
    assert result["a"].note == "Superseded by a newer source (2026-09-01)."
    assert (result["a"].conflicts_with, result["b"].conflicts_with) == (("b",), ("a",))


def test_fatima_example_opposites() -> None:
    a = fact("a", "Me", "likes", "Fatima", (2024, 3, 1))
    b = fact("b", "Me", "dislikes", "Fatima", (2026, 11, 1))
    assert statuses([a, b]) == {"a": Status.SUPERSEDED, "b": Status.CURRENT}


def test_names_are_compared_case_insensitively() -> None:
    old = fact("a", "Sara Khan", "manages_account", "harbor hotels group", (2025, 3, 10))
    new = fact("b", *BILAL, (2026, 9, 1))
    assert statuses([old, new])["a"] is Status.SUPERSEDED


# --- 2. Source authority (same date) --------------------------------------------------------------


def test_contract_beats_email_on_the_same_day() -> None:
    a = fact("a", *SARA, (2026, 9, 1), [("mail", "email")])
    b = fact("b", *BILAL, (2026, 9, 1), [("contract", "contract")])
    conflict = detect_conflicts([a, b], RULES)[0]
    assert (conflict.decided_by, conflict.winner) == (DecidedBy.AUTHORITY, "b")
    assert statuses([a, b]) == {"a": Status.SUPERSEDED, "b": Status.CURRENT}


def test_time_comes_before_authority() -> None:
    """A newer email beats an older contract: CLAUDE.md applies time first."""
    a = fact("a", *SARA, (2025, 1, 1), [("contract", "contract")])
    b = fact("b", *BILAL, (2026, 1, 1), [("mail", "email")])
    assert detect_conflicts([a, b], RULES)[0].decided_by is DecidedBy.TIME
    assert statuses([a, b])["b"] is Status.CURRENT


def test_agent_notes_rank_lowest() -> None:
    assert RULES.authority_rank("contract") < RULES.authority_rank("email")
    assert RULES.authority_rank("email") < RULES.authority_rank("agent")
    assert RULES.authority_rank("unknown kind") > RULES.authority_rank("agent")


# --- 3. Corroboration ----------------------------------------------------------------------------


def test_more_independent_documents_win() -> None:
    a = fact("a", *SARA, (2026, 9, 1), [("r1", "report"), ("r2", "report")])
    b = fact("b", *BILAL, (2026, 9, 1), [("r3", "report")])
    conflict = detect_conflicts([a, b], RULES)[0]
    assert (conflict.decided_by, conflict.winner) == (DecidedBy.CORROBORATION, "a")


def test_the_same_document_twice_is_not_corroboration() -> None:
    a = fact("a", *SARA, (2026, 9, 1), [("r1", "report"), ("r1", "report")])
    b = fact("b", *BILAL, (2026, 9, 1), [("r3", "report")])
    assert detect_conflicts([a, b], RULES)[0].decided_by is DecidedBy.UNDECIDED


# --- 4. Disputed, decisions, history ---------------------------------------------------------------


def test_undecidable_conflict_is_disputed() -> None:
    a = fact("a", *SARA, (2026, 9, 1))
    b = fact(
        "b",
        *BILAL,
        (
            2026,
            9,
            1,
        ),
    )
    conflict = detect_conflicts([a, b], RULES)[0]
    assert conflict.open and conflict.explanation == "no rule decides; review needed"
    assert statuses([a, b]) == {"a": Status.DISPUTED, "b": Status.DISPUTED}


def test_reviewer_decision_overrides_every_rule() -> None:
    old = fact("a", *SARA, (2025, 3, 10))
    new = fact("b", *BILAL, (2026, 9, 1))
    decisions = {frozenset(("a", "b")): "a"}  # the reviewer knows the memo was cancelled
    conflict = detect_conflicts([old, new], RULES, decisions)[0]
    assert (conflict.decided_by, conflict.winner) == (DecidedBy.PERSON, "a")
    result = {f.id: f for f in classify([old, new], RULES, decisions)}
    assert result["b"].status is Status.SUPERSEDED
    assert result["b"].note == "Superseded: a reviewer chose the other fact."


def test_ended_facts_are_history_and_the_change_is_recorded() -> None:
    old = fact("a", *SARA, (2025, 3, 10), ended=datetime(2026, 9, 1, tzinfo=UTC))
    new = fact("b", *BILAL, (2026, 9, 1))
    conflict = detect_conflicts([old, new], RULES)[0]
    assert (conflict.decided_by, conflict.winner) == (DecidedBy.ENDED, "b")
    result = {f.id: f for f in classify([old, new], RULES)}
    assert (result["a"].status, result["b"].status) == (Status.HISTORY, Status.CURRENT)
    assert result["b"].conflicts_with == ("a",)


def test_unrelated_facts_stay_current() -> None:
    facts = [
        fact("a", *SARA, (2025, 1, 1)),
        fact("b", "Sara Khan", "manages_account", "Crestview Bank", (2026, 1, 1)),
        fact("c", "Ali", "works_for", "Brightline", (2025, 1, 1)),
    ]
    assert set(statuses(facts).values()) == {Status.CURRENT}
    assert detect_conflicts(facts, RULES) == []


def test_order_is_preserved() -> None:
    facts = [fact(str(i), f"P{i}", "works_for", "X", (2025, 1, 1)) for i in range(5)]
    assert [f.id for f in classify(facts, RULES)] == ["0", "1", "2", "3", "4"]


# --- Source types used by the authority rule ------------------------------------------------------


def source_type(file: str | None, title: str = "Doc", kind: str = "document") -> str:
    return Provenance(
        doc_id="d", document_id="x", source="s:x", title=title, file=file, kind=kind,
        observed_at=datetime.now(UTC), business_function="Sales",
    ).source_type  # fmt: skip


def test_source_types() -> None:
    assert source_type("2025_supply_agreement.pdf") == "contract"
    assert source_type("handover.eml") == "email"
    assert source_type("q2.xlsx") == "spreadsheet"
    assert source_type("deck.pptx") == "presentation"
    assert source_type("2026-09-15_account_reassignment_memo.docx") == "memo"
    assert source_type(None, "customers 1-25", kind="database") == "database"
    assert source_type(None, "note", kind="agent") == "agent"
    assert source_type("notes.docx") == "document"
