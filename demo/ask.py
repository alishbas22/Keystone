"""Command-line version of the demo: load the sample documents and ask a question.

uv run python demo/ask.py --load            # ingest demo/data (skips files already loaded)
uv run python demo/ask.py "Who manages Harbor Hotels Group?"
"""

import argparse
import asyncio
from datetime import UTC, datetime
from pathlib import Path

from keystone.net import configure_network

configure_network()

from keystone.config import get_settings  # noqa: E402
from keystone.graph.engine import ingest_document, open_graph  # noqa: E402
from keystone.graph.qa import answer_question  # noqa: E402
from keystone.ingest.parse import SUPPORTED_SUFFIXES, date_from_filename, parse_file  # noqa: E402

DATA = Path(__file__).parent / "data"
FUNCTIONS = {
    "company_overview": "HR",
    "account_plan": "Sales",
    "customer_report": "Sales",
    "finance_review": "Finance",
    "reassignment_memo": "Sales",
}


async def load() -> None:
    async with open_graph() as graph:
        await graph.build_indices_and_constraints()
        for path in sorted(p for p in DATA.iterdir() if p.suffix in SUPPORTED_SUFFIXES):
            doc = parse_file(path)
            function = next((f for k, f in FUNCTIONS.items() if k in path.name), "Sales")
            when = date_from_filename(path.name) or datetime.now(UTC)
            print(f"{path.name}: {len(doc.sections)} sections ({function}, {when.date()})")

            def progress(i: int, total: int, heading: str) -> None:
                print(f"   [{i}/{total}] {heading}")

            stored = await ingest_document(graph, doc, function, when, progress, get_settings())
            if not stored:
                print("   already in the graph, skipped")


async def ask(question: str) -> None:
    async with open_graph() as graph:
        answer = await answer_question(graph, question)
    print(f"\nQ: {question}\nA: {answer.text}\n")
    for n, fact in enumerate(answer.facts, start=1):
        if n in answer.cited:
            files = {answer.sources[u].ref.file for u in fact.sources}
            print(f"[{n}] {fact.status:<10} {fact.text}  <- {', '.join(sorted(files))}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("question", nargs="?")
    parser.add_argument("--load", action="store_true", help="ingest the sample documents")
    args = parser.parse_args()
    if args.load:
        asyncio.run(load())
    if args.question:
        asyncio.run(ask(args.question))


if __name__ == "__main__":
    main()
