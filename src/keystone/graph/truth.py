"""Truth engine (minimal KAN-25): label facts as current, history, superseded or disputed.

- history     Graphiti already marked the fact as ended (invalid_at / expired_at).
- superseded  The ontology rules say it contradicts a NEWER current fact (time rule).
- disputed    It contradicts a current fact with the same date, so neither wins.
- current     Otherwise.

Source authority and corroboration rules come later (full KAN-25).
"""

from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum

from keystone.ontology.models import RelationRules, Triple


class Status(StrEnum):
    CURRENT = "current"
    HISTORY = "history"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"


@dataclass(frozen=True)
class Fact:
    id: str
    subject: str
    relation: str  # ontology relation name, e.g. "manages_account"
    object: str
    text: str
    observed_at: datetime | None  # when the fact became true / was recorded
    ended_at: datetime | None = None  # set when Graphiti invalidated the fact
    sources: tuple[str, ...] = ()
    status: Status = Status.CURRENT
    note: str = ""
    conflicts_with: tuple[str, ...] = field(default=())

    @property
    def triple(self) -> Triple:
        return Triple(self.subject.lower(), self.relation, self.object.lower())


def classify(facts: list[Fact], rules: RelationRules) -> list[Fact]:
    """Return the facts with status, note and conflicts filled in (input order kept)."""
    result = {
        f.id: replace(f, status=Status.HISTORY, note="Ended according to a later source.")
        if f.ended_at
        else f
        for f in facts
    }
    live = [f for f in result.values() if f.status is Status.CURRENT]
    for i, a in enumerate(live):
        for b in live[i + 1 :]:
            reason = rules.contradicts(a.triple, b.triple)
            if reason is None:
                continue
            _mark(result, a, b, reason)
    return [result[f.id] for f in facts]


def _mark(result: dict[str, Fact], a: Fact, b: Fact, reason: str) -> None:
    a, b = result[a.id], result[b.id]
    a_conf = (*a.conflicts_with, b.id)
    b_conf = (*b.conflicts_with, a.id)
    if a.observed_at and b.observed_at and a.observed_at != b.observed_at:
        older, newer = (a, b) if a.observed_at < b.observed_at else (b, a)
        result[older.id] = replace(
            older,
            status=Status.SUPERSEDED,
            note=f"Contradicted by a newer fact ({reason}).",
            conflicts_with=a_conf if older is a else b_conf,
        )
        result[newer.id] = replace(newer, conflicts_with=b_conf if newer is b else a_conf)
        return
    for fact, conf in ((a, a_conf), (b, b_conf)):
        if fact.status is not Status.SUPERSEDED:
            result[fact.id] = replace(
                fact,
                status=Status.DISPUTED,
                note=f"Conflicts with another fact of the same date ({reason}).",
                conflicts_with=conf,
            )
