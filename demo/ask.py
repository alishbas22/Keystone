"""Command line: load the sample documents into the graph and ask a question.

    uv run python demo/ask.py --load            # the core demo (5 documents)
    uv run python demo/ask.py --load --all      # all 30 documents (slow on the free tier)
    uv run python demo/ask.py "Who manages Harbor Hotels Group?"

Business functions come from the folder each document is filed in (demo/data/<Function>/).
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
CORE = {
    "2025-01-15_company_overview.pdf",
    "2025-03-10_harbor_hotels_account_plan.docx",
    "2025-06-30_q2_customer_report.xlsx",
    "2025-09-02_q3_finance_review.pptx",
    "2026-09-15_account_reassignment_memo.docx",
}


async def load(everything: bool) -> None:
    async with open_graph() as graph:
        await graph.setup()
        for document in FolderConnector({"path": DATA}, "samples").read():
            if not everything and document.file_name not in CORE:
                continue
            result = await ingest_document(graph, document, None, print)
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
    parser.add_argument("--all", action="store_true", help="with --load: all 30 documents")
    args = parser.parse_args()
    if args.load:
        asyncio.run(load(args.all))
    if args.question:
        asyncio.run(ask(args.question))


if __name__ == "__main__":
    main()
