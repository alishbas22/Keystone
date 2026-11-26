"""Evaluation against the ground truth (KAN-37): extraction, resolution, conflicts, scope, latency.

Scoring functions are pure (tested without a graph); `collect_*` functions read a live graph.
Names are compared after normalising (case, punctuation, legal suffixes) and a name counts as a
match when all words of the shorter name appear in the longer one ("DHL" ~ "DHL Freight").
"""

import statistics
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from typing import Any

from graphiti_core.edges import EntityEdge

from keystone.graph import queries
from keystone.graph.engine import KeystoneGraph
from keystone.graph.facts import build_facts, find_conflicts
from keystone.graph.provenance import SOURCE_LABEL
from keystone.graph.resolution import SAME_AS, normalise

Triple = tuple[str, str, str]


# --- Matching -----------------------------------------------------------------------------------


def names_match(a: str, b: str) -> bool:
    na, nb = normalise(a).split(), normalise(b).split()
    if not na or not nb:
        return False
    short, long = sorted((na, nb), key=len)
    return all(word in long for word in short)


def triple_matches(expected: Triple, actual: Triple) -> bool:
    return (
        expected[1] == actual[1]
        and names_match(expected[0], actual[0])
        and names_match(expected[2], actual[2])
    )


# --- Scores -------------------------------------------------------------------------------------


@dataclass
class ExtractionScore:
    recall: float | None
    precision: float | None
    found: int
    expected: int
    correct_extracted: int
    extracted_in_complete_docs: int
    missing: list[tuple[str, Triple]] = field(default_factory=list)
    unexpected: list[tuple[str, Triple]] = field(default_factory=list)


def score_extraction(
    expected: dict[str, list[Triple]],
    extracted: dict[str, list[Triple]],
    complete_docs: set[str],
) -> ExtractionScore:
    """Recall over all evaluated documents; precision over documents whose facts are complete."""
    found = total = correct = considered = 0
    missing: list[tuple[str, Triple]] = []
    unexpected: list[tuple[str, Triple]] = []
    for doc, facts in expected.items():
        if doc not in extracted:
            continue  # not ingested: not evaluated
        actual = extracted[doc]
        for fact in facts:
            total += 1
            if any(triple_matches(fact, a) for a in actual):
                found += 1
            else:
                missing.append((doc, fact))
        if doc in complete_docs:
            for a in actual:
                considered += 1
                if any(triple_matches(f, a) for f in facts):
                    correct += 1
                else:
                    unexpected.append((doc, a))
    return ExtractionScore(
        recall=found / total if total else None,
        precision=correct / considered if considered else None,
        found=found,
        expected=total,
        correct_extracted=correct,
        extracted_in_complete_docs=considered,
        missing=missing,
        unexpected=unexpected,
    )


@dataclass
class ResolutionScore:
    accuracy: float | None
    checked: int
    failures: list[str] = field(default_factory=list)


def score_resolution(
    clusters: dict[str, str], same: list[list[str]], different: list[list[str]]
) -> ResolutionScore:
    """`clusters` maps every entity name to its merge-group id (its own uuid if not merged)."""

    def cluster_of(name: str) -> str | None:
        """The cluster of the entity with this name: exact match first, else the closest one."""
        target = normalise(name)
        for entity, cluster in clusters.items():
            if normalise(entity) == target:
                return cluster
        partial = sorted(
            (abs(len(normalise(entity)) - len(target)), cluster)
            for entity, cluster in clusters.items()
            if names_match(name, entity)
        )
        return partial[0][1] if partial else None

    checked, failures = 0, []
    for a, b in same:
        ca, cb = cluster_of(a), cluster_of(b)
        if ca is None or cb is None:
            continue
        checked += 1
        if ca != cb:
            failures.append(f"should be one entity: {a} / {b}")
    for a, b in different:
        ca, cb = cluster_of(a), cluster_of(b)
        if ca is None or cb is None:
            continue
        checked += 1
        if ca == cb:
            failures.append(f"wrongly merged: {a} / {b}")
    return ResolutionScore(
        accuracy=(checked - len(failures)) / checked if checked else None,
        checked=checked,
        failures=failures,
    )


@dataclass
class ConflictResult:
    id: str
    evaluated: bool
    detected: bool = False
    expected_rule: str = ""
    rule: str | None = None
    expected_current: str | None = None
    current: str | None = None
    note: str = ""

    @property
    def correct(self) -> bool:
        return (
            self.detected
            and self.rule == self.expected_rule
            and (self.expected_current is None) == (self.current is None)
            and (
                self.expected_current is None
                or names_match(self.expected_current, self.current or "")
            )
        )


def score_conflicts(
    planted: list[dict[str, Any]],
    found: list[tuple[str, Triple, Triple, str | None]],
    ingested_docs: set[str],
) -> list[ConflictResult]:
    """`found`: (rule, fact A, fact B, winning triple's distinguishing value or None)."""
    results = []
    for p in planted:
        docs = {c["document"] for c in p["claims"]}
        if not docs <= ingested_docs:
            results.append(ConflictResult(p["id"], evaluated=False, note="documents not ingested"))
            continue
        claims = [
            (c["subject"], c.get("relation", p.get("relation", "")), c["object"])
            for c in p["claims"]
        ]
        match = next(
            (
                f
                for f in found
                if any(triple_matches(c, f[1]) for c in claims)
                and any(triple_matches(c, f[2]) for c in claims)
            ),
            None,
        )
        result = ConflictResult(
            p["id"], evaluated=True, expected_rule=p["rule"], expected_current=p["expected_current"]
        )
        if match:
            result.detected, result.rule, result.current = True, match[0], match[3]
        results.append(result)
    return results


# --- Reading the live graph ---------------------------------------------------------------------


async def extracted_by_document(
    graph: KeystoneGraph, documents: list[str]
) -> dict[str, list[Triple]]:
    """Facts extracted from each document (by file name), using merged entity names."""
    rows = await graph.query(
        f"MATCH (s:{SOURCE_LABEL} {{group_id: $group}}) WHERE s.file IN $docs "
        f"MATCH ()-[e:RELATES_TO {{group_id: $group}}]->() WHERE s.episode_uuid IN e.episodes "
        f"RETURN s.file AS doc, collect(DISTINCT e.uuid) AS edges",
        group=graph.group,
        docs=documents,
    )
    loaded = await graph.query(
        f"MATCH (s:{SOURCE_LABEL} {{group_id: $group}}) WHERE s.file IN $docs "
        f"RETURN DISTINCT s.file AS doc",
        group=graph.group,
        docs=documents,
    )
    result: dict[str, list[Triple]] = {str(r["doc"]): [] for r in loaded}
    for row in rows:
        edges = await EntityEdge.get_by_uuids(graph.graphiti.driver, [str(u) for u in row["edges"]])  # type: ignore[attr-defined]
        facts, _ = await build_facts(graph, edges)
        result[str(row["doc"])] = [(f.subject, f.relation, f.object) for f in facts]
    return result


async def entity_clusters(graph: KeystoneGraph) -> dict[str, str]:
    rows = await graph.query(
        f"MATCH (n:Entity {{group_id: $group}}) "
        f"OPTIONAL MATCH (n)-[:{SAME_AS}*1..10]-(m:Entity) "
        f"WITH n, collect(DISTINCT m.uuid) + [n.uuid] AS ids "
        f"RETURN n.name AS name, "
        f"  reduce(lo = head(ids), x IN ids | CASE WHEN x < lo THEN x ELSE lo END) AS cluster",
        group=graph.group,
    )
    return {str(r["name"]): str(r["cluster"]) for r in rows}


async def detected_conflicts(
    graph: KeystoneGraph, scope: list[str]
) -> list[tuple[str, Triple, Triple, str | None]]:
    conflicts, facts, _ = await find_conflicts(graph, scope)
    found = []
    for c in conflicts:
        a, b = facts.get(c.a), facts.get(c.b)
        if a is None or b is None:
            continue
        winner = facts.get(c.winner) if c.winner else None
        found.append(
            (
                str(c.decided_by),
                (a.subject, a.relation, a.object),
                (b.subject, b.relation, b.object),
                _what_differs(winner, b if winner is a else a) if winner else None,
            )
        )
    return found


def _what_differs(winner: Any, other: Any) -> str:
    """The part of the winning fact that differs from the losing one (who / what / relation)."""
    for mine, theirs in (
        (winner.subject, other.subject),
        (winner.object, other.object),
        (winner.relation, other.relation),
    ):
        if mine != theirs:
            return str(mine)
    return str(winner.subject)


async def scope_leaks(graph: KeystoneGraph, rules: dict[str, Any]) -> list[str]:
    """Facts a business function must not see but does (should be empty)."""
    leaks = []
    for function, rule in rules.items():
        edges = await queries.scoped_edges(graph, "true", [function])
        facts, _ = await build_facts(graph, edges)
        visible = [(f.subject, f.relation, f.object) for f in facts]
        for forbidden in rule.get("must_not_see", []):
            subject, relation, obj = forbidden
            if any(triple_matches((subject, relation, obj), v) for v in visible):
                leaks.append(f"{function} sees {forbidden}")
    return leaks


async def latency(action: Callable[[], Awaitable[Any]], runs: int = 5) -> float:
    """Median seconds of `runs` calls (after one warm-up call)."""
    await action()
    timings = []
    for _ in range(runs):
        start = time.perf_counter()
        await action()
        timings.append(time.perf_counter() - start)
    return statistics.median(timings)
