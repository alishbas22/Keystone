# Keystone

[![CI](https://github.com/alishbas22/Keystone/actions/workflows/ci.yml/badge.svg)](https://github.com/alishbas22/Keystone/actions/workflows/ci.yml)

**Open-source business ontology layer for AI agents.**
*Every fact has a source. Every answer has proof.*

Keystone connects a company's documents, email and databases, maps them onto one standard
business ontology, and builds a knowledge graph in which every fact keeps its source, date and
confidence. Contradictions between documents are detected and resolved (history is kept), and the
graph is exposed to AI agents over MCP with access scoped by business function (Sales, Finance,
HR, Operations).

> Status: early development (MVP). See [CLAUDE.md](CLAUDE.md) for the full design.

## Built on

[Graphiti](https://github.com/getzep/graphiti) (temporal knowledge graph) ·
[Neo4j](https://neo4j.com) · [Docling](https://github.com/docling-project/docling) (document parsing) ·
[MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk) ·
[rdflib](https://github.com/RDFLib/rdflib) with Schema.org, W3C ORG and PROV-O.

## Quick start

Requirements: [Docker Desktop](https://www.docker.com/products/docker-desktop/) and
[uv](https://docs.astral.sh/uv/) (uv installs the right Python, 3.12, for you).

```bash
# 1. Configure secrets
cp .env.example .env          # then edit .env and set NEO4J_PASSWORD

# 2. Start the stack (Neo4j + Keystone API)
docker compose up -d --build

# 3. Check it works
curl http://localhost:8000/health
```

- Keystone API: http://localhost:8000 (interactive docs at http://localhost:8000/docs)
- Neo4j Browser: http://localhost:7474 (user `neo4j`, password from `.env`)

### Demo: upload documents and ask questions

Needs an OpenAI API key in `.env` (`OPENAI_API_KEY=sk-...`) and Neo4j running
(`docker compose up -d`).

```bash
uv run streamlit run ui/app.py          # opens http://localhost:8501
```

Click **Load the sample documents** (5 files from a fictional company, with one planted
contradiction), then ask *"Who is our biggest customer and who manages their account?"*.
The same from the command line:

```bash
uv run python demo/ask.py --load "Who manages Harbor Hotels Group?"
```

To regenerate the sample files: `uv run python demo/make_sample_docs.py`.

### Connecting data sources

Built-in connectors: `folder` (documents) and `sql` (PostgreSQL and other databases). To add
your own system, see the [connector guide](docs/connectors.md); a template generates a working
connector project in one command.

### Local development

```bash
uv sync                 # create .venv with Python 3.12 and install dependencies
uv run pytest           # run tests
uv run ruff check .     # lint
uv run mypy src         # type check
```

## Repository layout

```
ontology/            Core ontology, interfaces, relation rules, industry packs (YAML)
src/keystone/
  ontology/          Ontology loading and conversion to Graphiti types
  connectors/        Connector SDK (base class, registry) and built-in connectors
  ingest/            Parsing (Docling) and the ingestion pipeline
  graph/             Graphiti engine, entity resolution, truth engine, scopes
  mcp_server/        MCP tools, agent keys, audit log
  api/               HTTP API used by the UI and for health checks
ui/                  Web UI
demo/                Sample company data and the two-agent demo
eval/                Accuracy evaluation
tests/               Tests
```

## Licence

Apache-2.0. See [LICENSE](LICENSE).
