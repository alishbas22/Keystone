"""Command line: load the sample documents into the graph and ask a question.

uv run python demo/ask.py --load            # ingest demo/data (skips unchanged files)
uv run python demo/ask.py "Who manages Harbor Hotels Group?"
"""

import argparse
import asyncio
from pathlib import Path

from keystone.net import configure_network

configure_network()

from keystone.connectors.folder import FolderConnector  # noqa: E402
from keystone.graph.engine import open_graph  # noqa: E402
from keystone.graph.qa import answer_question  # noqa: E402
from keystone.ingest.pipeline import ingest_document  # noqa: E402

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
        await graph.setup()
        for document in FolderConnector({"path": DATA}, "samples").read():
            function = next((f for k, f in FUNCTIONS.items() if k in document.id), "Sales")
            result = await ingest_document(graph, document, function, print)
            print(f"{document.id}: {result.status} {result.message}".strip())


async def ask(question: str) -> None:
    async with open_graph() as graph:
        answer = await answer_question(graph, question)
    print(f"\nQ: {question}\nA: {answer.text}\n")
    for n, fact in enumerate(answer.facts, start=1):
        if n in answer.cited:
            where = "; ".join(sorted({answer.sources[u].location for u in fact.sources}))
            print(f"[{n}] {fact.status:<10} {fact.text}  <- {where}")


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
