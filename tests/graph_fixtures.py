"""A small Brightline graph built with Graphiti's own models (no LLM), for query tests.

Sales:   2025 account plan  -> Sara Khan MANAGES_ACCOUNT Harbor Hotels Group;
                               Harbor Hotels Group CUSTOMER_OF Brightline
         2026 memo          -> Bilal Ahmed MANAGES_ACCOUNT Harbor Hotels Group   (contradiction)
Finance: invoice review     -> INV-2025-031 BILLED_TO Harbor Hotels Group
HR:      performance review -> Sara Khan REPORTS_TO Omar Siddiqui              (HR only)
"""

from dataclasses import dataclass
from datetime import UTC, datetime

from graphiti_core.edges import EntityEdge, EpisodicEdge
from graphiti_core.nodes import EntityNode, EpisodeType, EpisodicNode

from keystone.graph.engine import KeystoneGraph
from tests.neo4j_support import provenance


@dataclass
class Brightline:
    episodes: dict[str, str]
    entities: dict[str, str]
    facts: dict[str, str]


VECTOR = [0.1] * 1024  # placeholder embedding: Graphiti requires one, tests need no LLM


def _dt(y: int, m: int, d: int) -> datetime:
    return datetime(y, m, d, tzinfo=UTC)


async def build_brightline(graph: KeystoneGraph) -> Brightline:
    driver = graph.graphiti.driver
    group = graph.group

    episodes: dict[str, str] = {}
    for key, function, when in [
        ("plan", "Sales", _dt(2025, 3, 10)),
        ("memo", "Sales", _dt(2026, 9, 15)),
        ("invoice", "Finance", _dt(2025, 9, 2)),
        ("review", "HR", _dt(2025, 12, 1)),
    ]:
        episode = EpisodicNode(
            name=key, group_id=group, source=EpisodeType.text, source_description="test",
            content=f"{key} text", valid_at=when, created_at=when,
        )  # fmt: skip
        await episode.save(driver)
        await graph.record_provenance(
            episode.uuid,
            provenance(key, function=function, observed_at=when, file=f"{key}.docx"),
        )
        episodes[key] = episode.uuid

    entities: dict[str, str] = {}
    mentions = {
        "Harbor Hotels Group": ("Organization", ["plan", "memo", "invoice"]),
        "Brightline Furnishings": ("Organization", ["plan"]),
        "Sara Khan": ("Person", ["plan", "review"]),
        "Bilal Ahmed": ("Person", ["memo"]),
        "Omar Siddiqui": ("Person", ["review"]),
        "INV-2025-031": ("Invoice", ["invoice"]),
    }
    for name, (label, in_episodes) in mentions.items():
        node = EntityNode(
            name=name, group_id=group, labels=[label], summary=f"{name} summary",
            name_embedding=VECTOR,
        )  # fmt: skip
        await node.save(driver)
        entities[name] = node.uuid
        for key in in_episodes:
            await EpisodicEdge(
                source_node_uuid=episodes[key], target_node_uuid=node.uuid, group_id=group,
                created_at=_dt(2026, 1, 1),
            ).save(driver)  # fmt: skip

    facts: dict[str, str] = {}
    for key, (a, rel, b, episode, valid) in {
        "sara_manages": (
            "Sara Khan",
            "MANAGES_ACCOUNT",
            "Harbor Hotels Group",
            "plan",
            _dt(2025, 3, 10),
        ),
        "bilal_manages": (
            "Bilal Ahmed",
            "MANAGES_ACCOUNT",
            "Harbor Hotels Group",
            "memo",
            _dt(2026, 9, 1),
        ),
        "customer_of": (
            "Harbor Hotels Group",
            "CUSTOMER_OF",
            "Brightline Furnishings",
            "plan",
            _dt(2025, 3, 10),
        ),
        "billed_to": (
            "INV-2025-031",
            "BILLED_TO",
            "Harbor Hotels Group",
            "invoice",
            _dt(2025, 9, 2),
        ),
        "reports_to": ("Sara Khan", "REPORTS_TO", "Omar Siddiqui", "review", _dt(2025, 12, 1)),
    }.items():
        edge = EntityEdge(
            source_node_uuid=entities[a], target_node_uuid=entities[b], name=rel,
            fact=f"{a} {rel.lower().replace('_', ' ')} {b}", group_id=group,
            episodes=[episodes[episode]], created_at=valid, valid_at=valid,
            fact_embedding=VECTOR,
        )  # fmt: skip
        await edge.save(driver)
        facts[key] = edge.uuid
    return Brightline(episodes, entities, facts)
