# Keystone architecture

Keystone turns a company's documents and databases into a knowledge graph that AI agents can
query through MCP. Every fact keeps its source, date and business function; contradictions are
resolved by explicit rules; agents only see the business functions their key allows.

## Data flow

```
 Sources                 Connector SDK              Parsing                Extraction
 ───────                 ─────────────              ───────                ──────────
 folder (PDF, Word,  ─►  check / discover / read ─► Docling: sections  ─►  Graphiti + the ontology
 Excel, PowerPoint,      → Document (bytes or       with heading and        (entity and relation
 email, MD, CSV)         text, date, author,        pages; email sender      types generated from
 SQL database            business function)         and date                 the YAML)
 your connector                                                                    │
                                                                                   ▼
 Agents ◄── MCP server ◄── scoped queries ◄── truth engine ◄── Neo4j knowledge graph
 (any MCP    (8 tools,      (filter by         (time, authority,   Entity, RELATES_TO (facts),
  client)    agent keys,    business function  corroboration,      Episodic + KeystoneSource
             audit log)     inside Cypher)     reviewer decisions) (provenance), SAME_AS (merges)

 Streamlit UI: documents · graph explorer · conflicts · sources & agents · ask a question
```

## Components

| Package | Role |
|---|---|
| `ontology/` (YAML) + `keystone.ontology` | Core business ontology (Schema.org, W3C ORG, PROV-O), interfaces (Party, Locatable, Transactable), contradiction rules, industry packs (retail). Validated with Pydantic; every `maps_to` checked against the official vocabularies with rdflib. Converted to Graphiti's Pydantic types (`to_graphiti`). |
| `keystone.connectors` | Connector SDK (Airbyte CDK pattern): `BaseConnector`, `Document`, `ConnectorManifest`, registry via Python entry points, contract checker, cookiecutter template. Built-ins: `folder`, `sql` (with NVIDIA auto-ontology style table mapping). |
| `keystone.ingest` | Docling parsing (`parse.py`) and the pipeline (`pipeline.py`): Document → sections → Graphiti episodes with provenance. |
| `keystone.graph` | Graphiti integration (`engine.py`), provenance (`provenance.py`), call pacing (`pacing.py`), entity resolution (`resolution.py`), truth engine (`truth.py`, `facts.py`), business function scopes (`scopes.py`), scoped queries (`queries.py`), agent claims (`claims.py`), question answering (`qa.py`). |
| `keystone.mcp_server` | MCP server (`server.py`, MCP Python SDK v2 `MCPServer`), agent keys (`auth.py`), audit log (`audit.py`), CLI to manage agents (`agents.py`). |
| `keystone.evaluation` + `eval/` | Scoring against `demo/ground_truth.yaml`; `eval/run_eval.py` writes `eval/REPORT.md`. |
| `ui/` | Streamlit multi-page app. |

## Key design decisions

### Every fact has a source: Keystone's own provenance records

Each document section becomes one Graphiti *episode*. Keystone attaches a `KeystoneSource` node
to it (`(:Episodic)-[:HAS_PROVENANCE]->(:KeystoneSource)`) with source, file, section, pages,
author, observed date, business function, confidence and kind. Graphiti does not persist custom
episode metadata on Neo4j and rewrites the properties of its own nodes and edges when it updates
them (`SET n = $data`), so provenance lives in nodes Graphiti never touches. Every fact (edge)
lists the episodes it came from, so its sources are always reachable.

### Business function tags are inherited, not copied

A fact carries the business functions of the episodes it came from; an entity carries those of
every episode that mentions it. Tags therefore cannot go stale or be wiped by Graphiti. The same
Cypher conditions (`scopes.py`) are used inside every query, so out-of-scope data never leaves the
database: filtering happens before results are returned, never by redacting afterwards.

Graphiti's free-text entity summaries are written from all documents and cannot be filtered by
business function, so scoped callers never see or search them.

### Claims, not facts: the truth engine

Facts are never overwritten. Two facts contradict when the ontology rules say so: opposite
relations on the same pair (`likes` / `dislikes`), or two values of a single-valued relation
(`manages_account` allows one manager per customer), also across entities merged by resolution.
Contradictions are resolved in order:

1. a reviewer's stored decision (conflict review page);
2. **time**: the newer claim wins, dated by its newest source document;
3. **source authority**: `rules.yaml` ranks source kinds (contract > database > policy > report >
   spreadsheet > presentation > memo > document > email > chat > agent);
4. **corroboration**: more independent documents win (an agent repeating itself counts once);
5. otherwise both facts are **disputed**.

Statuses: current, history (ended by a later source), superseded, disputed. Every outcome records
the rule that decided it, so agents can explain answers.

### Entity resolution is conservative and reversible

Graphiti merges duplicates while ingesting (names, embeddings, the ontology's identity properties
such as email and SKU). Keystone adds a second pass: normalised names, fuzzy similarity, initials
and identity values. Only near-certain pairs (score ≥ 0.97) merge automatically, as `SAME_AS`
links that can be removed again; nothing is deleted. Other candidates are shown for review in the
graph explorer.

### Scoped access with agent keys

Agents are stored as `KeystoneAgent` nodes with the SHA-256 hash of their key (the key itself is
shown once) and their business functions. Over HTTP the MCP server checks
`Authorization: Bearer <key>` with a `TokenVerifier` (MCP auth); over stdio the key comes from
`KEYSTONE_AGENT_KEY`. Without a valid key a tool call is denied; there is no "see everything"
fallback. Agents can write facts back (`add_claim`) only into their own business functions; claims
carry the agent as author and the lowest source authority. Every call is recorded in
`KeystoneAudit` nodes (agent, tool, arguments, scope, time, outcome, returned entity and fact ids).

### Working within LLM rate limits

Graphiti makes many LLM calls per section. Keystone paces calls to a requests-per-minute budget
(`LLM_REQUESTS_PER_MINUTE`, default 10 for the Gemini free tier) and retries single calls on rate
limits, overloads and timeouts. Gemini defaults are models available to new API keys
(`gemini-3.5-flash-lite`, `gemini-embedding-001`); Graphiti's built-in Gemini defaults are not.

## Storage (Neo4j labels)

| Label / relationship | Written by | Holds |
|---|---|---|
| `Entity` (+ type label, e.g. `Person`) | Graphiti | entities and their ontology properties |
| `RELATES_TO` | Graphiti | facts, with validity dates and source episodes |
| `Episodic`, `MENTIONS` | Graphiti | stored sections and the entities they mention |
| `KeystoneSource`, `HAS_PROVENANCE` | Keystone | provenance of each episode |
| `SAME_AS` | Keystone | reversible entity merges |
| `KeystoneDecision` | Keystone | reviewers' choices between contradicting facts |
| `KeystoneAgent` | Keystone | agents, key hashes and business functions |
| `KeystoneAudit` | Keystone | agent access log |

All Keystone data of one deployment shares a `group_id` (`GRAPH_GROUP`, default `keystone`).

## Deviations from the original plan (CLAUDE.md)

- Code lives in `src/keystone/` (one package) instead of top-level `connectors/`, `graph/`… to
  avoid clashing with other libraries; `keystone-connectors` is a sub-package for now.
- The SQL connector uses SQLAlchemy, so PostgreSQL and other databases share one connector.
- Business function tags are inherited from provenance rather than stored on nodes (see above).
- Streamlit was chosen for the UI.
- Default LLM is Gemini (one key covers extraction, embeddings and reranking); OpenAI also works.

## Known limitations

- **Entity properties** (e.g. a person's email) are aggregated by Graphiti from all documents and
  are shown to any agent that can see the entity; only relations (facts) are filtered per source.
- **Free LLM tiers are slow:** ingesting the 30 sample documents takes hours on the Gemini free
  tier; paid tiers can raise `LLM_REQUESTS_PER_MINUTE` and `LLM_MAX_CONCURRENCY`.
- **The UI has no login.** It is an administrator tool for a trusted local machine.
- **Single tenant:** one Neo4j database per deployment (multi-tenant hosting is not in the MVP).
- On Windows with antivirus HTTP inspection, the newest MCP protocol version over HTTP may fail;
  the standard initialize handshake works (Linux CI tests both).
