"""Entity resolution (KAN-24): find duplicate entities and merge them reversibly.

Graphiti already merges most duplicates while it ingests (similar names, embeddings and the
ontology's identity properties). This module is a second, conservative pass over the graph:

1. `score_pair` compares two entities of the same type: normalised names (case, punctuation,
   legal suffixes such as "Ltd"), fuzzy similarity, initials ("S. Khan" ~ "Sara Khan") and shared
   identity values (same email or SKU = same thing).
2. Merges are REVERSIBLE: nothing is deleted. A `SAME_AS` relationship links the two entities
   with the score, reason, author and time. `unmerge` removes it.
3. Only near-certain pairs are merged automatically; the rest are returned for human review.
"""

import re
import unicodedata
from dataclasses import dataclass
from datetime import UTC, datetime
from itertools import combinations
from typing import Any

from rapidfuzz import fuzz

from keystone.graph.engine import KeystoneGraph

SAME_AS = "SAME_AS"
AUTO_MERGE_THRESHOLD = 0.97  # conservative: wrong merges are worse than missed ones
REVIEW_THRESHOLD = 0.85

LEGAL_SUFFIXES = {
    "ltd", "limited", "inc", "incorporated", "llc", "plc", "corp", "corporation", "co",
    "company", "gmbh", "ag", "sa", "bv", "pty", "llp", "lp",
}  # fmt: skip


@dataclass(frozen=True)
class EntityRecord:
    uuid: str
    name: str
    entity_type: str
    attributes: dict[str, Any]


@dataclass(frozen=True)
class MergeCandidate:
    a: EntityRecord
    b: EntityRecord
    score: float
    reason: str

    @property
    def auto(self) -> bool:
        return self.score >= AUTO_MERGE_THRESHOLD


def normalise(name: str) -> str:
    """'HARBOR HOTELS GROUP LTD.' -> 'harbor hotels group'."""
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode()
    words = re.sub(r"[^a-z0-9 ]+", " ", text.lower().replace("&", " and ")).split()
    while words and words[-1] in LEGAL_SUFFIXES:
        words.pop()
    return " ".join(words)


def _initials_match(a: str, b: str) -> bool:
    """'s khan' ~ 'sara khan': same surname, first names compatible as initials."""
    wa, wb = a.split(), b.split()
    if len(wa) != len(wb) or len(wa) < 2 or wa[-1] != wb[-1]:
        return False
    return all(x == y or (len(x) == 1 and y.startswith(x)) or (len(y) == 1 and x.startswith(y))
               for x, y in zip(wa[:-1], wb[:-1], strict=True))  # fmt: skip


def score_pair(
    a: EntityRecord, b: EntityRecord, identity_properties: list[str]
) -> tuple[float, str]:
    """Similarity 0..1 between two entities of the same type, with the main reason."""
    for prop in identity_properties:
        va, vb = a.attributes.get(prop), b.attributes.get(prop)
        if va and vb:
            if str(va).strip().lower() == str(vb).strip().lower():
                return 1.0, f"same {prop}"
            return 0.0, f"different {prop}"  # two different SKUs are never the same product
    na, nb = normalise(a.name), normalise(b.name)
    if not na or not nb:
        return 0.0, "empty name"
    if na == nb:
        return 0.98, "same name after normalising"
    if a.entity_type == "Person" and _initials_match(na, nb):
        return 0.9, "initials match"
    similarity = fuzz.token_sort_ratio(na, nb) / 100
    if _words_contained(na, nb):  # "harbor hotels" inside "harbor hotels group"
        similarity = max(similarity, 0.88)
    return round(similarity, 3), "similar names"


def _words_contained(a: str, b: str) -> bool:
    """The shorter name's words appear, in order and as whole words, inside the longer one."""
    short, long = sorted((a.split(), b.split()), key=len)
    if len(" ".join(short)) < 6:  # too short to be meaningful ("acme")
        return False
    n = len(short)
    return any(long[i : i + n] == short for i in range(len(long) - n + 1))


def find_candidates(
    entities: list[EntityRecord], identity_properties: dict[str, list[str]]
) -> list[MergeCandidate]:
    """Likely duplicate pairs of the same type, best first."""
    by_type: dict[str, list[EntityRecord]] = {}
    for entity in entities:
        by_type.setdefault(entity.entity_type, []).append(entity)
    candidates = []
    for entity_type, group in by_type.items():
        for a, b in combinations(group, 2):
            score, reason = score_pair(a, b, identity_properties.get(entity_type, []))
            if score >= REVIEW_THRESHOLD:
                candidates.append(MergeCandidate(a, b, score, reason))
    return sorted(candidates, key=lambda c: -c.score)


# --- Graph operations ----------------------------------------------------------------------------


async def load_entities(graph: KeystoneGraph) -> list[EntityRecord]:
    rows = await graph.query(
        "MATCH (n:Entity {group_id: $group}) "
        "RETURN n.uuid AS uuid, n.name AS name, labels(n) AS labels, properties(n) AS props",
        group=graph.group,
    )
    known = set(graph.ontology.entity_types)
    records = []
    for row in rows:
        labels = [str(label) for label in row["labels"]]  # type: ignore[attr-defined]
        entity_type = next((label for label in labels if label in known), "Entity")
        records.append(
            EntityRecord(str(row["uuid"]), str(row["name"]), entity_type, dict(row["props"]))  # type: ignore[call-overload]
        )
    return records


async def merge(
    graph: KeystoneGraph, a: str, b: str, reason: str, score: float, by: str = "keystone"
) -> None:
    """Record that two entities are the same thing. Reversible with `unmerge`."""
    await graph.query(
        f"MATCH (a:Entity {{uuid: $a}}), (b:Entity {{uuid: $b}}) "
        f"MERGE (a)-[r:{SAME_AS}]-(b) "
        f"SET r.reason = $reason, r.score = $score, r.created_by = $by, "
        f"r.created_at = $now, r.group_id = $group",
        a=a,
        b=b,
        reason=reason,
        score=score,
        by=by,
        now=datetime.now(UTC),
        group=graph.group,
    )


async def unmerge(graph: KeystoneGraph, a: str, b: str) -> None:
    await graph.query(
        f"MATCH (:Entity {{uuid: $a}})-[r:{SAME_AS}]-(:Entity {{uuid: $b}}) DELETE r", a=a, b=b
    )


async def same_as(graph: KeystoneGraph, uuid: str) -> list[str]:
    """All entities merged with this one (directly or through others), including itself."""
    rows = await graph.query(
        f"MATCH (n:Entity {{uuid: $uuid}}) "
        f"OPTIONAL MATCH (n)-[:{SAME_AS}*1..10]-(m:Entity) "
        f"RETURN collect(DISTINCT m.uuid) AS others",
        uuid=uuid,
    )
    others = [str(u) for u in (rows[0]["others"] if rows else [])]  # type: ignore[attr-defined]
    return sorted({uuid, *others})


async def canonical_names(graph: KeystoneGraph, uuids: list[str]) -> dict[str, str]:
    """entity uuid -> the name used for its whole merge group (the longest name)."""
    rows = await graph.query(
        f"MATCH (n:Entity) WHERE n.uuid IN $uuids "
        f"OPTIONAL MATCH (n)-[:{SAME_AS}*1..10]-(m:Entity) "
        f"RETURN n.uuid AS uuid, n.name AS name, collect(DISTINCT m.name) AS others",
        uuids=uuids,
    )
    result = {}
    for row in rows:
        names = [str(row["name"]), *[str(x) for x in row["others"]]]  # type: ignore[attr-defined]
        result[str(row["uuid"])] = max(names, key=lambda s: (len(s), s))
    return result


async def resolve_duplicates(graph: KeystoneGraph, apply: bool = True) -> list[MergeCandidate]:
    """Find duplicates; merge the near-certain ones (if `apply`); return all candidates."""
    candidates = find_candidates(
        await load_entities(graph),
        {name: entity.identity_properties for name, entity in graph.ontology.entity_types.items()},
    )
    if apply:
        for c in candidates:
            if c.auto:
                await merge(graph, c.a.uuid, c.b.uuid, c.reason, c.score, by="keystone:auto")
    return candidates
