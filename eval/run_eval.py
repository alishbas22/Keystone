"""Evaluate Keystone against demo/ground_truth.yaml (KAN-37) and write eval/REPORT.md.

    uv run python eval/run_eval.py                         # score what is already loaded
    uv run python eval/run_eval.py --load-contradictions   # first load the planted-contradiction docs
    uv run python eval/run_eval.py --questions             # also ask the questions (LLM calls)

Only documents that are in the graph are scored; the report says which were evaluated.
"""

import argparse
import asyncio
import json
import logging
import shutil
import tempfile
import time
from dataclasses import asdict
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml

from keystone.net import configure_network

configure_network()

from keystone import evaluation as ev  # noqa: E402
from keystone.connectors.folder import FolderConnector  # noqa: E402
from keystone.graph import queries  # noqa: E402
from keystone.graph.engine import KeystoneGraph, open_graph  # noqa: E402
from keystone.graph.qa import answer_question  # noqa: E402
from keystone.ingest.pipeline import ingest_document  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]
TRUTH = yaml.safe_load((ROOT / "demo" / "ground_truth.yaml").read_text(encoding="utf-8"))
ALL = ["Sales", "Finance", "HR", "Operations"]


async def load_contradiction_documents(graph: KeystoneGraph) -> None:
    wanted = {c["document"] for p in TRUTH["contradictions"] for c in p["claims"]}
    loaded = {d.file for d in await graph.list_documents()}
    for document in FolderConnector({"path": ROOT / "demo" / "data"}, "samples").read():
        if document.file_name in wanted and document.file_name not in loaded:
            result = await ingest_document(graph, document, None, print)
            print(f"{document.id}: {result.status} {result.message}".strip())


async def onboarding_seconds() -> float | None:
    """Time to generate a connector from the template and pass the contract checks."""
    try:
        import importlib
        import sys

        from cookiecutter.main import cookiecutter

        from keystone.connectors.testing import check_connector
    except ImportError:
        return None
    start = time.perf_counter()
    out = Path(tempfile.mkdtemp())
    try:
        project = Path(cookiecutter(str(ROOT / "templates" / "connector"), no_input=True, output_dir=str(out),
                                    extra_context={"connector_name": "eval-crm", "display_name": "Eval CRM"}))  # fmt: skip
        sys.path.insert(0, str(project / "src"))
        module = importlib.import_module("keystone_eval_crm")
        connector = module.EvalCRMConnector({"data_file": project / "sample_data" / "records.json"})
        check_connector(connector)
        return time.perf_counter() - start
    finally:
        shutil.rmtree(out, ignore_errors=True)


async def evaluate(graph: KeystoneGraph, ask: bool) -> dict[str, Any]:
    docs = list(TRUTH["facts"])
    expected = {d: [tuple(f) for f in TRUTH["facts"][d]["facts"]] for d in docs}
    complete = {d for d in docs if TRUTH["facts"][d].get("complete")}
    extracted = await ev.extracted_by_document(graph, docs)
    ingested = set(extracted)

    extraction = ev.score_extraction(expected, extracted, complete)  # type: ignore[arg-type]
    resolution = ev.score_resolution(
        await ev.entity_clusters(graph), TRUTH["same_entity"], TRUTH["different"]
    )
    conflicts = ev.score_conflicts(
        TRUTH["contradictions"], await ev.detected_conflicts(graph, ALL), ingested
    )
    leaks = await ev.scope_leaks(graph, TRUTH["scope"])

    harbor = await queries.search_entities(graph, "Harbor Hotels Group", ALL, 1)
    timings = {
        "search_entities": await ev.latency(lambda: queries.search_entities(graph, "Harbor", ALL)),
        "get_conflicts": await ev.latency(lambda: queries.get_conflicts(graph, ALL)),
    }
    if harbor:
        timings["get_entity"] = await ev.latency(
            lambda: queries.get_entity(graph, harbor[0].uuid, ALL)
        )

    answers = []
    if ask:
        for q in TRUTH["questions"]:
            answer = await answer_question(graph, q["question"])
            cited = [answer.facts[n - 1] for n in answer.cited]
            answers.append({
                "question": q["question"],
                "answer": answer.text,
                "grounded": bool(cited) and all(f.sources for f in cited),
                "mentions_expected": all(word.lower() in answer.text.lower() for word in q["must_mention"]),
            })  # fmt: skip

    return {
        "at": datetime.now(UTC).isoformat(timespec="seconds"),
        "documents_evaluated": sorted(ingested),
        "extraction": asdict(extraction),
        "resolution": asdict(resolution),
        "conflicts": [{**asdict(c), "correct": c.correct} for c in conflicts],
        "scope_leaks": leaks,
        "latency_seconds": timings,
        "answers": answers,
        "onboarding_seconds": await onboarding_seconds(),
    }


def pct(value: float | None) -> str:
    return "n/a" if value is None else f"{value:.0%}"


def report(r: dict[str, Any]) -> str:
    ex, res = r["extraction"], r["resolution"]
    evaluated = [c for c in r["conflicts"] if c["evaluated"]]
    correct = sum(1 for c in evaluated if c["correct"])
    grounded = [a for a in r["answers"] if a["grounded"]]
    slowest = max(r["latency_seconds"].values()) if r["latency_seconds"] else None
    lines = [
        "# Keystone evaluation report",
        "",
        f"Run {r['at']} on {len(r['documents_evaluated'])} documents of the sample company "
        "(`demo/ground_truth.yaml`). Generated by `eval/run_eval.py`.",
        "",
        "| Metric (CLAUDE.md section 19) | Result | Target |",
        "|---|---|---|",
        f"| Extraction recall (key facts found) | {pct(ex['recall'])} ({ex['found']}/{ex['expected']}) | 80%+ |",
        f"| Extraction precision (documents with complete labels) | {pct(ex['precision'])} ({ex['correct_extracted']}/{ex['extracted_in_complete_docs']}) | 80%+ |",
        f"| Entity resolution accuracy | {pct(res['accuracy'])} ({res['checked']} pairs checked) | high |",
        f"| Planted contradictions found and resolved by the right rule | {correct}/{len(evaluated)} | all |",
        f"| Scope leaks (facts a function must not see) | {len(r['scope_leaks'])} | 0 |",
        f"| Answers grounded in cited sources | {len(grounded)}/{len(r['answers'])} | all |" if r["answers"] else "| Answers grounded in cited sources | not run (`--questions`) | all |",
        f"| Slowest query (median) | {slowest:.3f} s | < 1 s |" if slowest is not None else "| Slowest query | n/a | < 1 s |",
        f"| Time to onboard a new data source (template + contract tests) | {r['onboarding_seconds']:.1f} s of tooling; ~1 h of developer work expected | < 1 h |" if r["onboarding_seconds"] else "| Time to onboard a new data source | n/a | < 1 h |",
        "",
        "## Contradictions",
        "",
        "| Planted | Evaluated | Detected | Rule (expected) | Current (expected) | Correct |",
        "|---|---|---|---|---|---|",
    ]  # fmt: skip
    for c in r["conflicts"]:
        if not c["evaluated"]:
            lines.append(f"| {c['id']} | no: {c['note']} | - | - | - | - |")
            continue
        lines.append(
            f"| {c['id']} | {'yes' if c['evaluated'] else 'no: ' + c['note']} | "
            f"{'yes' if c['detected'] else 'no'} | {c['rule'] or '-'} ({c['expected_rule'] or '-'}) | "
            f"{c['current'] or '-'} ({c['expected_current'] or 'disputed'}) | {'✅' if c['correct'] else '❌' if c['evaluated'] else '-'} |"
        )
    lines += ["", "## Extraction misses", ""]
    lines += [f"- {doc}: {' '.join(fact)}" for doc, fact in ex["missing"]] or ["- none"]
    lines += ["", "## Unexpected facts (documents with complete labels)", ""]
    lines += [f"- {doc}: {' '.join(fact)}" for doc, fact in ex["unexpected"]] or ["- none"]
    lines += ["", "## Entity resolution problems", ""]
    lines += [f"- {f}" for f in res["failures"]] or ["- none"]
    lines += ["", "## Scope leaks", ""]
    lines += [f"- {leak}" for leak in r["scope_leaks"]] or ["- none"]
    if r["answers"]:
        lines += ["", "## Answers", ""]
        for a in r["answers"]:
            lines.append(
                f"- **{a['question']}** {a['answer']} (grounded: {a['grounded']}, mentions expected: {a['mentions_expected']})"
            )
    lines += ["", "## Latency (median of 5 runs)", ""]
    lines += [
        f"- {name}: {seconds * 1000:.0f} ms" for name, seconds in r["latency_seconds"].items()
    ]
    lines += ["", "## Documents evaluated", ""] + [f"- {d}" for d in r["documents_evaluated"]]
    return "\n".join(lines) + "\n"


async def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--load-contradictions", action="store_true")
    parser.add_argument("--questions", action="store_true")
    args = parser.parse_args()
    for noisy in ("neo4j", "graphiti_core", "httpx", "httpx2"):
        logging.getLogger(noisy).setLevel(logging.ERROR)
    async with open_graph() as graph:
        if args.load_contradictions:
            await load_contradiction_documents(graph)
        results = await evaluate(graph, args.questions)
    out = ROOT / "eval"
    (out / "results.json").write_text(json.dumps(results, indent=2, default=str), encoding="utf-8")
    (out / "REPORT.md").write_text(report(results), encoding="utf-8")
    print(report(results))


if __name__ == "__main__":
    asyncio.run(main())
