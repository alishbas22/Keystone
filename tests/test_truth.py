"""Minimal truth engine: current / history / superseded / disputed."""

from datetime import UTC, datetime

from keystone.graph.truth import Fact, Status, classify
from keystone.ontology import core_ontology

RULES = core_ontology().rules


def fact(
    id: str,
    subject: str,
    relation: str,
    obj: str,
    year: int,
    month: int = 1,
    ended: datetime | None = None,
) -> Fact:
    return Fact(
        id=id,
        subject=subject,
        relation=relation,
        object=obj,
        text=f"{subject} {relation} {obj}",
        observed_at=datetime(year, month, 1, tzinfo=UTC),
        ended_at=ended,
    )


def by_id(facts: list[Fact]) -> dict[str, Fact]:
    return {f.id: f for f in facts}


def test_planted_contradiction_newer_account_manager_wins() -> None:
    """The demo dataset: 2025 plan says Sara, 2026 memo says Bilal manages Harbor Hotels."""
    old = fact("a", "Sara Khan", "manages_account", "Harbor Hotels Group", 2025, 3)
    new = fact("b", "Bilal Ahmed", "manages_account", "Harbor Hotels Group", 2026, 9)
    result = by_id(classify([old, new], RULES))
    assert result["a"].status is Status.SUPERSEDED
    assert result["a"].conflicts_with == ("b",)
    assert result["b"].status is Status.CURRENT
    assert result["b"].conflicts_with == ("a",)


def test_names_are_compared_case_insensitively() -> None:
    old = fact("a", "Sara Khan", "manages_account", "harbor hotels group", 2025)
    new = fact("b", "Bilal Ahmed", "manages_account", "Harbor Hotels Group", 2026)
    assert by_id(classify([old, new], RULES))["a"].status is Status.SUPERSEDED


def test_fatima_example_opposites() -> None:
    a = fact("a", "Me", "likes", "Fatima", 2024, 3)
    b = fact("b", "Me", "dislikes", "Fatima", 2026, 11)
    result = by_id(classify([a, b], RULES))
    assert (result["a"].status, result["b"].status) == (Status.SUPERSEDED, Status.CURRENT)


def test_same_date_conflict_is_disputed() -> None:
    a = fact("a", "Sara Khan", "manages_account", "Harbor Hotels Group", 2026, 9)
    b = fact("b", "Bilal Ahmed", "manages_account", "Harbor Hotels Group", 2026, 9)
    result = classify([a, b], RULES)
    assert [f.status for f in result] == [Status.DISPUTED, Status.DISPUTED]


def test_ended_facts_are_history_and_do_not_conflict() -> None:
    ended = datetime(2026, 9, 1, tzinfo=UTC)
    old = fact("a", "Sara Khan", "manages_account", "Harbor Hotels Group", 2025, ended=ended)
    new = fact("b", "Bilal Ahmed", "manages_account", "Harbor Hotels Group", 2026, 9)
    result = by_id(classify([old, new], RULES))
    assert result["a"].status is Status.HISTORY
    assert result["b"].status is Status.CURRENT
    assert result["b"].conflicts_with == ()


def test_unrelated_facts_stay_current() -> None:
    facts = [
        fact("a", "Sara Khan", "manages_account", "Harbor Hotels Group", 2025),
        fact("b", "Sara Khan", "manages_account", "Crestview Bank", 2026),
        fact("c", "Ali", "works_for", "Brightline", 2025),
    ]
    assert {f.status for f in classify(facts, RULES)} == {Status.CURRENT}


def test_order_is_preserved() -> None:
    facts = [fact(str(i), f"P{i}", "works_for", "X", 2025) for i in range(5)]
    assert [f.id for f in classify(facts, RULES)] == ["0", "1", "2", "3", "4"]
