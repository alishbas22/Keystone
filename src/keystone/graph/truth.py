"""Truth engine (KAN-25): detect contradictions and decide which claim is current.

Claims are never overwritten or deleted. Each fact gets a status:

- current     believed true now
- history     ended: Graphiti invalidated it when a later source said it stopped being true
- superseded  contradicted by another current fact that won under the rules below
- disputed    contradicted, and no rule could decide

Two facts contradict when the ontology rules say so (opposite relations on the same pair, or two
values for a single-valued relation; see rules.yaml). Contradictions are resolved in order:

1. a person's decision (from the conflict review page)
2. time: the newer claim wins (claims within `same_time` count as simultaneous)
3. source authority: the more trusted kind of source wins (rules.yaml `source_authority`)
4. corroboration: the claim backed by more independent documents wins
5. otherwise both are disputed
"""

from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from enum import StrEnum

from keystone.ontology.models import RelationRules, Triple

SAME_TIME = timedelta(days=1)


class Status(StrEnum):
    CURRENT = "current"
    HISTORY = "history"
    SUPERSEDED = "superseded"
    DISPUTED = "disputed"


class DecidedBy(StrEnum):
    PERSON = "person"
    TIME = "time"
    AUTHORITY = "source authority"
    CORROBORATION = "corroboration"
    ENDED = "ended by a later source"
    UNDECIDED = "undecided"


@dataclass(frozen=True)
class Evidence:
    """One source supporting a fact."""

    doc_id: str
    source_type: str  # contract, database, email... (see Provenance.source_type)
    observed_at: datetime | None
    location: str = ""


@dataclass(frozen=True)
class Fact:
    id: str
    subject: str
    relation: str  # ontology relation name, e.g. "manages_account"
    object: str
    text: str
    observed_at: datetime | None  # when the claim was made (newest source); used by the time rule
    ended_at: datetime | None = None  # set when Graphiti invalidated the fact
    valid_from: datetime | None = None  # when it became true per the text ("effective 1 Sep")
    sources: tuple[str, ...] = ()  # episode uuids
    evidence: tuple[Evidence, ...] = ()
    status: Status = Status.CURRENT
    note: str = ""
    conflicts_with: tuple[str, ...] = field(default=())

    @property
    def triple(self) -> Triple:
        return Triple(self.subject.lower(), self.relation, self.object.lower())

    @property
    def documents(self) -> int:
        """Independent documents supporting the fact (corroboration)."""
        return len({e.doc_id for e in self.evidence}) or len(self.sources)


@dataclass(frozen=True)
class Conflict:
    a: str  # fact ids
    b: str
    reason: str  # which ontology rule makes them contradict
    decided_by: DecidedBy
    winner: str | None  # None when disputed
    explanation: str

    @property
    def open(self) -> bool:
        return self.winner is None


# A person's decision: {frozenset({fact_a, fact_b}): winner}
Decisions = dict[frozenset[str], str]


def detect_conflicts(
    facts: list[Fact],
    rules: RelationRules,
    decisions: Decisions | None = None,
    same_time: timedelta = SAME_TIME,
) -> list[Conflict]:
    """Every contradicting pair, with how it was (or was not) resolved."""
    decisions = decisions or {}
    conflicts = []
    for i, a in enumerate(facts):
        for b in facts[i + 1 :]:
            reason = rules.contradicts(a.triple, b.triple)
            if reason is None:
                continue
            conflicts.append(_resolve(a, b, reason, rules, decisions, same_time))
    return conflicts


def _resolve(
    a: Fact,
    b: Fact,
    reason: str,
    rules: RelationRules,
    decisions: Decisions,
    same_time: timedelta,
) -> Conflict:
    def decided(winner: Fact, by: DecidedBy, why: str) -> Conflict:
        return Conflict(a.id, b.id, reason, by, winner.id, why)

    decision = decisions.get(frozenset((a.id, b.id)))
    if decision in (a.id, b.id):
        return decided(a if decision == a.id else b, DecidedBy.PERSON, "decided by a reviewer")

    if bool(a.ended_at) != bool(b.ended_at):  # one already ended: a change, not a dispute
        live = b if a.ended_at else a
        return decided(live, DecidedBy.ENDED, "the older fact was ended by a later source")

    if a.observed_at and b.observed_at and abs(a.observed_at - b.observed_at) > same_time:
        newer = a if a.observed_at > b.observed_at else b
        return decided(newer, DecidedBy.TIME, f"newer source ({newer.observed_at:%Y-%m-%d})")

    rank_a = min((rules.authority_rank(e.source_type) for e in a.evidence), default=99)
    rank_b = min((rules.authority_rank(e.source_type) for e in b.evidence), default=99)
    if rank_a != rank_b:
        stronger = a if rank_a < rank_b else b
        kind = min(stronger.evidence, key=lambda e: rules.authority_rank(e.source_type))
        return decided(
            stronger, DecidedBy.AUTHORITY, f"more authoritative source ({kind.source_type})"
        )

    if a.documents != b.documents:
        more = a if a.documents > b.documents else b
        return decided(
            more, DecidedBy.CORROBORATION, f"claim confirmed by {more.documents} documents"
        )

    return Conflict(a.id, b.id, reason, DecidedBy.UNDECIDED, None, "no rule decides; review needed")


def classify(
    facts: list[Fact],
    rules: RelationRules,
    decisions: Decisions | None = None,
    same_time: timedelta = SAME_TIME,
) -> list[Fact]:
    """Return the facts with status, note and conflicts filled in (input order kept)."""
    result = {
        f.id: replace(f, status=Status.HISTORY, note="Ended according to a later source.")
        if f.ended_at
        else f
        for f in facts
    }
    for conflict in detect_conflicts(facts, rules, decisions, same_time):
        a, b = result[conflict.a], result[conflict.b]
        result[a.id] = replace(a, conflicts_with=(*a.conflicts_with, b.id))
        result[b.id] = replace(b, conflicts_with=(*b.conflicts_with, a.id))
        if conflict.decided_by is DecidedBy.ENDED:
            continue  # the ended fact is already history
        if conflict.winner is None:
            for fact_id in (a.id, b.id):
                fact = result[fact_id]
                if fact.status is Status.CURRENT:
                    result[fact_id] = replace(
                        fact, status=Status.DISPUTED, note=f"Disputed: {conflict.reason}."
                    )
            continue
        loser = b.id if conflict.winner == a.id else a.id
        result[loser] = replace(
            result[loser],
            status=Status.SUPERSEDED,
            note=f"Superseded by a {conflict.explanation}."
            if conflict.decided_by is not DecidedBy.PERSON
            else "Superseded: a reviewer chose the other fact.",
        )
    return [result[f.id] for f in facts]
