"""KAN-37: evaluation scoring (pure) and collection from a Neo4j test graph."""

from collections.abc import AsyncIterator
from typing import Any

import pytest_asyncio

from keystone import evaluation as ev
from keystone.graph.engine import KeystoneGraph
from tests.graph_fixtures import Brightline, build_brightline
from tests.neo4j_support import open_test_graph


def test_name_matching() -> None:
    assert ev.names_match("DHL", "DHL Freight")
    assert ev.names_match("Brightline Furnishings Ltd", "brightline furnishings")
    assert not ev.names_match("Sara Khan", "Bilal Ahmed")
    assert ev.triple_matches(
        ("Sara Khan", "manages_account", "Harbor Hotels"),
        ("Sara Khan", "manages_account", "Harbor Hotels Group"),
    )
    assert not ev.triple_matches(
        ("Sara Khan", "manages_account", "Harbor"), ("Sara Khan", "reports_to", "Harbor")
    )


def test_extraction_recall_and_precision() -> None:
    expected = {
        "a.docx": [
            ("Sara Khan", "manages_account", "Harbor Hotels Group"),
            ("Daniel Price", "works_for", "Harbor Hotels Group"),
        ],
        "b.eml": [("Harbor Hotels Group", "dislikes", "Ergo Desk Chair")],
        "not-loaded.pdf": [("X", "knows", "Y")],
    }
    extracted = {
        "a.docx": [("Sara Khan", "manages_account", "Harbor Hotels Group")],
        "b.eml": [
            ("Harbor Hotels Group", "dislikes", "Ergo Desk Chair"),
            ("Harbor Hotels Group", "relates_to", "Chair"),
        ],
    }
    score = ev.score_extraction(expected, extracted, complete_docs={"b.eml"})  # type: ignore[arg-type]
    assert (score.found, score.expected, score.recall) == (2, 3, 2 / 3)  # unloaded docs are skipped
    assert (score.correct_extracted, score.extracted_in_complete_docs, score.precision) == (
        1,
        2,
        0.5,
    )
    assert score.missing == [("a.docx", ("Daniel Price", "works_for", "Harbor Hotels Group"))]


def test_resolution_accuracy() -> None:
    clusters = {
        "Brightline Furnishings Ltd": "c1",
        "Brightline Furnishings": "c1",
        "Sara Khan": "c2",
        "Bilal Ahmed": "c2",
    }
    score = ev.score_resolution(
        clusters,
        same=[
            ["Brightline Furnishings Ltd", "Brightline Furnishings"],
            ["Missing Co", "Brightline Furnishings"],
        ],
        different=[["Sara Khan", "Bilal Ahmed"]],
    )
    assert (score.checked, score.accuracy) == (2, 0.5)  # pairs with an unknown name are skipped
    assert score.failures == ["wrongly merged: Sara Khan / Bilal Ahmed"]


def test_conflict_scoring() -> None:
    planted = [
        {"id": "time", "rule": "time", "relation": "manages_account", "expected_current": "Bilal Ahmed",
         "claims": [{"subject": "Sara Khan", "object": "Harbor", "document": "a"}, {"subject": "Bilal Ahmed", "object": "Harbor", "document": "b"}]},
        {"id": "disputed", "rule": "undecided", "relation": "subsidiary_of", "expected_current": None,
         "claims": [{"subject": "Crestview", "object": "Northstar", "document": "c"}, {"subject": "Crestview", "object": "Meridian", "document": "d"}]},
        {"id": "missing", "rule": "time", "relation": "likes", "expected_current": "x",
         "claims": [{"subject": "A", "object": "B", "document": "zzz"}]},
    ]  # fmt: skip
    found = [
        (
            "time",
            ("Sara Khan", "manages_account", "Harbor"),
            ("Bilal Ahmed", "manages_account", "Harbor"),
            "Bilal Ahmed",
        ),
        (
            "undecided",
            ("Crestview", "subsidiary_of", "Northstar"),
            ("Crestview", "subsidiary_of", "Meridian"),
            None,
        ),
    ]
    results = {r.id: r for r in ev.score_conflicts(planted, found, {"a", "b", "c", "d"})}  # type: ignore[arg-type]
    assert results["time"].correct and results["disputed"].correct
    assert not results["missing"].evaluated


@pytest_asyncio.fixture
async def world() -> AsyncIterator[tuple[KeystoneGraph, Brightline]]:
    async for graph in open_test_graph():
        yield graph, await build_brightline(graph)


async def test_collection_from_a_graph(world: Any) -> None:
    graph, _ = world
    extracted = await ev.extracted_by_document(graph, ["plan.docx", "memo.docx", "nothing.pdf"])
    assert set(extracted) == {"plan.docx", "memo.docx"}
    assert ("Sara Khan", "manages_account", "Harbor Hotels Group") in extracted["plan.docx"]
    found = await ev.detected_conflicts(graph, ["Sales", "Finance", "HR", "Operations"])
    assert [(f[0], f[3]) for f in found] == [("time", "Bilal Ahmed")]
    leaks = await ev.scope_leaks(
        graph,
        {"Finance": {"must_not_see": [["Sara Khan", "manages_account", "Harbor Hotels Group"]]}},
    )
    assert leaks == []
    clusters = await ev.entity_clusters(graph)
    assert len(set(clusters.values())) == len(clusters)  # nothing merged in the fixture
