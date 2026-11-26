# Keystone

[![CI](https://github.com/alishbas22/Keystone/actions/workflows/ci.yml/badge.svg)](https://github.com/alishbas22/Keystone/actions/workflows/ci.yml)

**Open-source business ontology layer for AI agents.**
*Every fact has a source. Every answer has proof.*

Keystone connects a company's documents and databases, maps them onto one standard business
ontology, and builds a knowledge graph in which every fact keeps its **source, date, confidence
and business function**. Contradictions between documents are detected and resolved by explicit
rules (history is kept), and the graph is exposed to any AI agent over **MCP**, with each agent
seeing only the business functions its key allows.

```
Q: Who is our biggest customer and who manages their account?
A: Harbor Hotels Group is Brightline Furnishings' biggest customer [3]. Their account was
   previously managed by Sara Khan until Bilal Ahmed took over effective 1 September 2026 [2, 6].

[2] current     Bilal Ahmed is the account manager ...  <- 2026-09-15_account_reassignment_memo.docx
[3] current     Harbor Hotels Group is ... biggest customer  <- 2025-03-10_harbor_hotels_account_plan.docx › Overview
[6] superseded  Sara Khan is the account manager ...    <- account plan › Account team; Q2 customer report (p. 1)
```

## What it does

1. **Connects** documents (PDF, Word, Excel, PowerPoint, email, Markdown, HTML, CSV, text),
   Gmail, Google Drive and databases (PostgreSQL and any SQLAlchemy database) through a small
   **connector SDK**.
2. **Maps** everything onto a **standard business ontology** built from Schema.org, W3C ORG and
   PROV-O, extended by **industry packs** (retail included).
3. **Builds a temporal knowledge graph** with [Graphiti](https://github.com/getzep/graphiti) on
   Neo4j, where every fact links back to the document, section, page, author and date it came from.
4. **Resolves contradictions**: newer claim, more authoritative source, more independent sources,
   or a reviewer's decision; otherwise *disputed*. Nothing is overwritten.
5. **Serves AI agents over MCP**: eight tools to search, explain, find paths, see conflicts and
   write facts back, shared between agents as common memory.
6. **Scopes access by business function** (Sales, Finance, HR, Operations) with agent keys,
   filtering inside the database query, and logs every access.

## Built on

[Graphiti](https://github.com/getzep/graphiti) · [Neo4j](https://neo4j.com) ·
[Docling](https://github.com/docling-project/docling) ·
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) ·
[rdflib](https://github.com/RDFLib/rdflib) (Schema.org, W3C ORG, PROV-O) ·
[SQLAlchemy](https://www.sqlalchemy.org) · [Streamlit](https://streamlit.io) · Gemini or OpenAI.

## Quick start

You need [Docker Desktop](https://www.docker.com/products/docker-desktop/),
[uv](https://docs.astral.sh/uv/) (it installs Python 3.12 for you) and a free
[Gemini API key](https://aistudio.google.com/apikey).

**1. Get the code and configure it**

```bash
git clone https://github.com/alishbas22/Keystone.git keystone
cd keystone
cp .env.example .env
```

Edit `.env`: set `NEO4J_PASSWORD` to any password and `GEMINI_API_KEY` to your key.

**2. Start the graph database and install Keystone**

```bash
docker compose up -d neo4j      # Neo4j at bolt://localhost:7687, browser at http://localhost:7474
uv sync                         # Python 3.12 environment with all dependencies
```

**3. Load the sample company and ask a question**

```bash
uv run python demo/ask.py --load "Who is our biggest customer and who manages their account?"
```

This loads 5 documents of a fictional company with a planted contradiction (about 10 minutes on
the free Gemini tier) and answers with sources. `--load --all` loads all 30 sample documents.

**4. Open the web UI**

```bash
uv run streamlit run ui/app.py  # http://localhost:8501
```

| Page | What you can do |
|---|---|
| Ask | Ask questions; see the answer, its facts, their status and sources |
| Documents | Upload files with a business function, preview parsing, ingest, delete, re-ingest |
| Graph explorer | Search entities, see connections, facts over time, sources, duplicates; *view as* a business function |
| Conflicts | Review contradictions and the rule that decided; keep A or B |
| Sources & agents | Connect a folder or database, create agent keys, read the audit log, check coverage |

**5. Connect AI agents**

```bash
uv run python -m keystone.mcp_server.agents create sales-bot --functions Sales   # prints a key once
uv run python -m keystone.mcp_server --http --port 8765                          # agents use Bearer keys
uv run python demo/two_agents.py                                                 # Sales + Finance agent demo
```

See [demo/two_agents.md](demo/two_agents.md) for the demo and for connecting Claude Desktop or any
MCP client. Agent tools: `describe_ontology`, `search_entities`, `get_entity`,
`get_relationships`, `find_path`, `get_conflicts`, `explain_claim`, `add_claim`.

### Notes for the free Gemini tier and Windows

- Keystone paces LLM calls (`LLM_REQUESTS_PER_MINUTE=10`) and retries rate limits, so loading is
  slow but steady. With billing enabled, raise it and set `LLM_MODEL=gemini-3.5-flash`.
- `LLM_PROVIDER=openai` with `OPENAI_API_KEY` works too.
- If downloads fail with certificate errors (antivirus HTTPS scanning), set `UV_SYSTEM_CERTS=true`;
  Keystone itself trusts the operating system's certificates.

## Use your own data

- **Documents:** upload on the *Documents* page, or connect a folder on *Sources & agents*.
  Subfolders named `Sales`, `Finance`, `HR` or `Operations` set the business function.
- **Gmail and Google Drive:** read-only connectors; one-time Google setup in
  [docs/google.md](docs/google.md), then connect on *Sources & agents*.
- **Databases:** connect on *Sources & agents*, or propose a mapping from the command line,
  review it and sync:
  `uv run python -m keystone.connectors.sql propose "postgresql+psycopg://user:pw@host/db" --owner "Your Company" --out mapping.yaml`
- **Other systems:** write a connector in about an hour with the template:
  [docs/connectors.md](docs/connectors.md).
- **Your industry:** add an ontology pack: [ontology/packs/README.md](ontology/packs/README.md).

## Evaluation

Measured against the labelled sample company (`demo/ground_truth.yaml`) with
`uv run python eval/run_eval.py`. Full report: [eval/REPORT.md](eval/REPORT.md).

Results on 14 documents (the core demo plus the documents behind all five planted
contradictions), Gemini `gemini-3.5-flash-lite` on the free tier:

| Metric | Result | Target |
|---|---|---|
| Extraction recall (labelled key facts found) | **85%** (28/33) | 80%+ |
| Extraction precision (documents with complete labels) | **73%** (8/11) | 80%+ |
| Entity resolution accuracy | **100%** (4 pairs) | high |
| Planted contradictions found and resolved by the intended rule | **3/5** | all |
| Scope leaks between business functions | **0** | 0 |
| Answers grounded in cited sources, mentioning the expected names | **3/3** | all |
| Slowest agent query (median) | **75 ms** | < 1 s |

What the misses tell us:

- Both failed contradictions are **extraction** errors, not truth-engine errors. In one email the
  model attributed the customer's opinion to the email's writer, so the *likes / dislikes* pair
  had different subjects; in another it extracted a generic link instead of `manages_account`,
  leaving one source against one, which Keystone correctly marks **disputed** for review rather
  than guessing.
- Precision misses are similar: an extra generic fact and a sender's company affiliation.
- The evaluation also found and fixed three real bugs before release: documents dated by file
  modification time instead of the date in their name, Graphiti's same-day "ended" decision
  overriding the source authority rule, and summaries leaking facts across business functions.
- Expected to improve with a stronger model (`LLM_MODEL=gemini-3.5-flash` on a paid tier) and
  more labelled documents.

## How it works

See [docs/architecture.md](docs/architecture.md): components, data flow, provenance, inherited
business function tags, the truth engine, entity resolution, agent keys and known limitations.

## Development

```bash
uv run pytest           # tests (Neo4j tests run when `docker compose up -d neo4j` is running)
uv run ruff check .     # lint
uv run ruff format .    # format
uv run mypy src         # type check
```

GitHub Actions runs all of these on every push with Neo4j and PostgreSQL services.

## Repository layout

```
ontology/             Core ontology, interfaces, rules, industry packs (YAML) and standard vocabularies
src/keystone/
  ontology/           Loading, validation, standards check, conversion to Graphiti types
  connectors/         Connector SDK, registry, folder and SQL connectors, contract tests
  ingest/             Docling parsing and the ingestion pipeline
  graph/              Graphiti engine, provenance, pacing, resolution, truth engine, scopes, queries
  mcp_server/         MCP server, agent keys, audit log
  evaluation.py       Scoring against the ground truth
  api/                Health-check API (Docker)
ui/                   Streamlit app (pages in ui/views/)
demo/                 Sample company (30 documents, sales database, ground truth), demo scripts
eval/                 Evaluation script and report
templates/connector/  Cookiecutter template for new connectors
docs/                 Architecture and connector guide
tests/                Tests
```

## Licence

Apache-2.0. See [LICENSE](LICENSE).
