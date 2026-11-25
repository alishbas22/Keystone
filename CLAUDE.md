# Keystone: Open-Source Business Ontology Layer for AI Agents

> Project context file for Claude Code. Put this file in the root of the repo as `CLAUDE.md` so Claude Code reads it automatically at the start of every session.

---

## 1. Project summary

**Keystone** is an open-source layer that:

1. **Connects** to any company's data sources (documents, email, Google Drive, databases).
2. **Maps** everything onto one standard **business ontology** (a shared model of business concepts such as Customer, Order, Employee, Product and how they relate).
3. **Builds a knowledge graph** in which every fact keeps its **source, date and confidence**.
4. **Handles contradictions** between documents (new facts supersede old ones, history is kept, unclear cases are marked "disputed").
5. **Exposes the graph to AI agents through MCP** (Model Context Protocol), so any open-source agent can connect and query it.
6. **Scopes access by business function**, so a Sales agent only sees Sales data and a Finance agent only sees Finance data.

**One-line pitch:** "Every fact has a source. Every answer has proof."

**Name meaning:** a keystone is the centre stone of an arch that holds all the other stones together. Keystone is the central layer that holds a company's data together for AI agents.

**Name history:** the project was first called "Sach Graph" ("sach" = truth). The supervisor did not like the name, so it was renamed **Keystone**. Do not use "Sach Graph" anywhere.

---

## 2. Original assignment (from supervisor)

1. Build an open-source business ontology layer.
   - Create an adapter and an open-source GitHub repo for defining business ontologies and building knowledge graphs from them.
   - Use the ontology as a semantic layer so agents can understand business concepts and their relationships.
   - Expose the layer through MCP so open-source agents can connect to it and query it.
2. Before coding:
   - Create a Miro board with the proposed design and share it for review. ✅ Done
   - After review, break the work into tasks on a Jira board and share it. ✅ Done
   - Then start coding. ⬅️ **We are here**
3. Reference repos given by the supervisor:
   - https://github.com/ModernRelay/omnigraph
   - https://github.com/NVIDIA/auto-ontology

---

## 3. Supervisor requirements and feedback (must be respected)

From the supervisor's chat messages:

- Make the ontology work for **global industries**: one platform that any company can use (like Microsoft software is used by many companies), not a single niche.
- Explain **business mapping**: "Imagine I have 50,000 docs, how would you get insights? It's a nightmare."
- **Connect anything** (any source) into the ontology layer.
- Handle **contradicting documents**. Example: one doc says "Fatima is my best friend", another says "I hate her". How do we know the current truth?
- **Do not build the ontology from scratch.** Look at existing ontology layers and put a good layer on top.
- Show **how much knowledge can be connected**.
- Sources: **email, Google Drive, databases**.
- **Connecting two agents** better (shared memory between agents).

From the supervisor's voice-note review of the Miro board (approved, start implementing), with these additions:

- **Standardised connector package / SDK** so other developers can integrate their own services into the platform.
- **Name the open-source stack / repos** being used. Do not build from scratch, given the time available.
- **User interface**: where people can upload and manage documents.
- **Which document types**: PDF, Excel, Word, PowerPoint, etc. Scope down at the start, but open-source libraries may already support many formats.
- **Scoped ontologies / access**: one agent must not have access to the entire business. Scope access by **business function**.
- Reference for ontology design: **Palantir Foundry ontology best practices**
  https://www.palantir.com/docs/foundry/ontology/ontology-best-practices

---

## 4. Links

| What | Link |
|---|---|
| Miro proposal board (final) | https://miro.com/app/board/uXjVELaVuiQ=/ |
| Jira board | https://alishba719.atlassian.net/jira/software/projects/KAN/boards |
| Jira issue list | https://alishba719.atlassian.net/browse/KAN |
| Palantir ontology best practices | https://www.palantir.com/docs/foundry/ontology/ontology-best-practices |
| Omnigraph | https://github.com/ModernRelay/omnigraph |
| NVIDIA auto-ontology | https://github.com/NVIDIA/auto-ontology |
| TrustGraph | https://github.com/trustgraph-ai/trustgraph |
| Graphiti (Zep) | https://github.com/getzep/graphiti |
| Docling (IBM) | https://github.com/docling-project/docling |
| Airbyte Python CDK (pattern for our SDK) | https://github.com/airbytehq/airbyte-python-cdk |
| Schema.org | https://schema.org |
| W3C ORG ontology | https://www.w3.org/TR/vocab-org/ |
| W3C PROV-O | https://www.w3.org/TR/prov-o/ |

Note: there are older Miro boards and a Canva deck from earlier drafts. They are outdated. The Miro board above is the final one.

---

## 5. Problem statement

Companies keep knowledge in thousands of documents, emails and databases. AI agents struggle because of:

- scattered data sources
- no shared business concepts (the same customer appears under five different names)
- no record of where a fact came from
- documents that contradict each other
- every industry rebuilding the same concepts
- agents that cannot share memory

**Core tension:** More data ↔ less clarity.

**Target audience:** AI engineers building business agents; enterprises with 50,000+ documents; small businesses without a data team; knowledge and data teams; open-source agent builders using MCP.

---

## 6. Architecture

```
Sources (PDF, Word, Excel, PowerPoint, email, Drive, SQL databases)
        │
        ▼
Connector SDK  (keystone-connectors: standard interface, plugins)
        │
        ▼
Parsing  (Docling → structured text with page/section references)
        │
        ▼
Ontology-guided extraction  (LLM constrained to ontology types)
        │                         ▲
        ▼                         │ guides
Entity resolution  (merge duplicates)     Ontology layer
        │                                 (core + industry packs + relation rules)
        ▼                         │ rules
Truth engine  (claims with source, date, confidence; conflict detection + resolution)
        │
        ▼
Knowledge graph  (Graphiti on Neo4j or FalkorDB)
        │
        ▼
MCP server  (tools for agents + scoped access filter + audit log)
        │
        ▼
Agents  (Sales agent, Finance agent, any MCP client)

Web UI: upload docs, connect sources, browse graph, review conflicts, set agent access
```

### Key components

| # | Component | Purpose |
|---|---|---|
| 01 | Universal connectors | Docs, email, Drive and SQL behind one interface |
| 02 | Ontology extraction | Turns text into typed entities and relations |
| 03 | Entity resolution | Merges duplicate people, companies, products |
| 04 | Truth engine | Keeps every claim with source and date, resolves conflicts |
| 05 | Industry packs | Standard core ontology extended per industry |
| 06 | MCP server | Any open-source agent can query and write |

### How it works (6 steps)

1. Connect data sources
2. Extract entities and relations
3. Merge duplicates
4. Store claims with source and date
5. Resolve contradictions
6. Agents query through MCP

---

## 7. Open-source foundation (build on these, do NOT build from scratch)

| Repo | Role in Keystone |
|---|---|
| **TrustGraph** | Reference for document ingestion and access control (filter results *before* the model reads them). Apache-2.0. |
| **Graphiti (Zep)** | Core graph engine. Temporal knowledge graph: facts have validity windows, keeps provenance to source data, supports custom ontology types via Pydantic, has an MCP server. Handles facts changing over time and contradictions. Apache-2.0. |
| **NVIDIA auto-ontology** | Approach for mapping relational databases (tables, columns, joins) onto ontology terms. Use as the pattern for the SQL connector. |
| **Omnigraph** | Typed graph store with branching for multi-agent work and a built-in MCP server. Candidate alternative backend later. |
| **Docling (IBM)** | Parses PDF, DOCX, PPTX, XLSX, HTML, email and more into structured output. MIT. |
| **Neo4j or FalkorDB** | Graph database behind Graphiti. |
| **Airbyte CDK pattern** | Proven model for our connector SDK design. |
| **MCP Python SDK** | Agent interface (FastMCP). |
| **rdflib** | Load standard ontologies (Schema.org, W3C ORG, PROV-O). |

**Important for Claude Code:** check each library's current docs and version before writing integration code. APIs change; do not assume method names from memory.

**Positioning:** parts of this idea already exist (TrustGraph, Graphiti). Keystone's value is combining them into one open-source layer with: a standard cross-industry ontology with industry packs, a connector SDK, contradiction handling with source authority rules, and scoped agent access by business function.

---

## 8. Ontology design

### Principles (from Palantir Foundry best practices)

1. **Model reality, not systems.** Customer, Order, Employee. Not table or department names.
2. **Model the domain, then map the data.** Core ontology first; connectors map source data into it.
3. **Separate identity from observation.** Facts and events are stored as dated claims linked to the entity.
4. **Links reflect real relationships.** "manages", "buys from", "reports to". Not join keys.
5. **Extend, don't modify.** Industry packs add types through shared interfaces; the core stays stable.
6. **Protect security boundaries.** Extending the model must never widen what an agent can see.

Also from Palantir: name things for humans (readable names, not cryptic abbreviations); keep object types focused (one distinct entity each); use interfaces for shared characteristics instead of wide sparse types; validate the ontology against real business questions.

### Core ontology (from standards)

- **Schema.org:** Person, Organization, Product, Order, Place, Event, CreativeWork/Document
- **W3C ORG:** roles, teams/units, membership, reporting lines
- **PROV-O:** source, author and time of every fact (provenance)

### Industry packs (extend the core)

- **Retail (first pack for MVP):** SKU, Supplier, Shipment, Store, Customer segment
- Later: Finance (Loan, Account, Guarantor), Healthcare (Patient, Appointment, Prescription)

### Relation rules

- Opposites: `likes` is the opposite of `dislikes` (used for conflict detection)
- Single-value relations: an employee has one current manager; an order belongs to exactly one customer

### Interfaces (examples)

- `Party` (Person or Organization), `Transactable`, etc., so packs extend without changing the core.

---

## 9. Contradiction handling (the "Fatima" example)

**Store claims, not facts. Never overwrite or delete.**

```
Claim A: Me likes Fatima (best friend)   source=doc_A.pdf  date=2024-03
Claim B: Me dislikes Fatima               source=doc_B.pdf  date=2026-11
```

1. **Detect:** opposite relations on the same pair (from relation rules).
2. **Resolve with rules, in order:**
   - **Time:** the newer claim is current, the older becomes history (Graphiti's validity windows handle this).
   - **Source authority:** e.g. an HR system beats a casual chat; a signed contract beats an email.
   - **Corroboration:** more independent sources win.
3. **If still unclear:** mark as **disputed** and show both claims with sources.

**Agent answer example:** "As of Nov 2026 you dislike Fatima (doc_B). In Mar 2024 doc_A said best friend. The relationship changed."

Claim model (conceptual):

```python
class Claim(BaseModel):
    subject: str         # "me"
    relation: str        # "likes" (opposite: "dislikes")
    object: str          # "fatima"
    source: str          # "doc_B.pdf"
    author: str          # person or agent who wrote it
    observed_at: date    # 2026-11-01
    confidence: float    # 0.8
    status: str          # current | history | disputed
    business_function: str  # Sales | Finance | HR | Operations
```

---

## 10. Connector SDK

Package name: **`keystone-connectors`**

1. **Install:** `pip install keystone-connectors`
2. **Implement one class:** `check()`, `discover()` and `read()` return standard `Document` objects
3. **Register:** add a manifest; the platform picks it up automatically (e.g. Python entry points)

Built-in connectors: **folder upload** and **PostgreSQL** (MVP); **Google Drive** and **Gmail** (next phase).
Provide a template (cookiecutter) and a short developer guide so outside developers can build a connector in under an hour.

---

## 11. Supported documents

- **MVP:** PDF, Word (.docx), Excel (.xlsx), PowerPoint (.pptx), Email (.eml), Markdown and text, all via Docling
- **Later:** scanned images (OCR), audio and video

Keep page/section references from parsing for provenance.

---

## 12. User interface

Simple web app (Next.js or Streamlit for MVP):

- Upload and manage documents (parsing status, delete, re-ingest)
- Connect sources in a few clicks
- Browse the knowledge graph (entity, relationships, facts over time, source docs)
- Review and resolve conflicts (pick the correct claim or keep both as history)
- Choose which agent sees what (agent keys and business function scopes)

---

## 13. Scoped access

- The full business ontology is split by **business function**: Sales, Finance, HR (and Operations).
- Every node/edge is tagged with its business function at ingestion.
- Each agent gets a **key tied to one or more business functions**.
- The MCP server **filters results before the agent sees them** (TrustGraph approach), never redacting afterwards.
- **Every access is logged** (agent, tool, scope, time, returned entities).

---

## 14. MCP tools

| Tool | What it does |
|---|---|
| `search_entities` | Find any entity by name or alias |
| `get_entity` | Full profile with current and past facts |
| `get_relationships` | Everything connected to an entity |
| `find_path` | How two entities are linked |
| `get_conflicts` | Open contradictions with evidence |
| `explain_claim` | Source document, author and date of a fact |
| `add_claim` | Agents write back what they learn (attributed to the agent) |
| `describe_ontology` | Concepts and rules, for agent planning |

All tools respect the calling agent's scope. Two agents sharing one server = shared memory, with every write attributed.

---

## 15. Tech stack

- Python 3.11+, Pydantic
- MCP Python SDK (FastMCP)
- Graphiti (temporal knowledge graph) on Neo4j or FalkorDB
- Docling for document parsing
- rdflib with Schema.org, W3C ORG, PROV-O
- Any LLM API with structured output for extraction (Claude, OpenAI or Gemini)
- Embeddings + fuzzy matching for entity resolution
- Docker Compose for local stack
- GitHub Actions CI: ruff, mypy, pytest
- Licence: Apache-2.0

---

## 16. Suggested repo structure

```
keystone/
├── CLAUDE.md                 # this file
├── README.md
├── LICENSE                   # Apache-2.0
├── pyproject.toml
├── docker-compose.yml        # Neo4j + Graphiti service + API
├── .env.example              # LLM API key, DB credentials
├── ontology/
│   ├── core.yaml             # Schema.org + ORG + PROV-O subset
│   ├── interfaces.yaml
│   ├── rules.yaml            # opposites, single-value relations
│   ├── packs/
│   │   └── retail.yaml
│   └── to_graphiti.py        # ontology → Pydantic entity/edge types
├── connectors/               # keystone-connectors package
│   ├── base.py               # BaseConnector, Document model
│   ├── registry.py           # manifest + auto-registration
│   ├── folder.py
│   ├── postgres.py
│   └── template/             # cookiecutter template
├── ingest/
│   ├── parse.py              # Docling
│   └── pipeline.py           # parse → extract → resolve → store
├── graph/
│   ├── engine.py             # Graphiti integration
│   ├── resolution.py         # entity resolution
│   ├── truth.py              # conflict detection + resolution rules
│   └── scopes.py             # business function tagging
├── mcp_server/
│   ├── server.py             # MCP tools
│   ├── auth.py               # agent keys → scopes
│   └── audit.py              # access log
├── ui/                       # Next.js or Streamlit
├── demo/
│   ├── data/                 # ~30 sample company docs + small sales DB
│   └── two_agents.md         # demo script
├── eval/                     # accuracy evaluation
└── tests/
```

---

## 17. Jira board (project key KAN, label `keystone`)

Site: https://alishba719.atlassian.net (project currently named "My Software Team"; rename to "Keystone" in Project settings).
Labels: `keystone` on everything, `mvp` = first version, `next-phase` = later.
KAN-1 to KAN-3 are old sample tasks and can be deleted.

### KAN-4 Epic: Project setup and infrastructure
- **KAN-11** Create GitHub repo with Apache-2.0 licence and project structure (High, mvp)
- **KAN-12** Docker Compose stack: Neo4j + Graphiti + API (High, mvp)
- **KAN-13** CI pipeline with GitHub Actions: lint, type check, tests (Medium)

### KAN-5 Epic: Core business ontology and industry packs
- **KAN-14** Define core ontology from Schema.org, W3C ORG and PROV-O (High, mvp)
- **KAN-15** Convert ontology to Pydantic entity and edge types for Graphiti (High, mvp)
- **KAN-16** Shared interfaces and relation rules (Medium, mvp)
- **KAN-17** First industry pack: retail (Medium, mvp)

### KAN-6 Epic: Ingestion and Connector SDK
- **KAN-18** Connector SDK: base class, document model and manifest (High, mvp)
- **KAN-19** Document parsing with Docling: PDF, Word, Excel, PowerPoint, email, text (High, mvp)
- **KAN-20** Built-in connectors: folder upload and PostgreSQL, SQL mapping following NVIDIA auto-ontology approach (High, mvp)
- **KAN-21** Connectors: Google Drive and Gmail (Low, next-phase)
- **KAN-22** Connector developer guide and template (Medium)

### KAN-7 Epic: Knowledge graph and truth engine
- **KAN-23** Integrate Graphiti as the graph engine (High, mvp)
- **KAN-24** Entity resolution for people, companies and products (High, mvp)
- **KAN-25** Contradiction resolution rules: time, source authority, corroboration (High, mvp)
- **KAN-26** Business function tags on every node and edge (Medium, mvp)

### KAN-8 Epic: MCP server and scoped access
- **KAN-27** MCP server with core read tools (High, mvp)
- **KAN-28** add_claim tool so agents can write back, with attribution (Medium, mvp)
- **KAN-29** Scoped access: agent keys tied to business functions (High, mvp)
- **KAN-30** Audit log of agent access (Medium)

### KAN-9 Epic: User interface
- **KAN-31** Document upload and management page (High, mvp)
- **KAN-32** Graph explorer page (Medium, mvp)
- **KAN-33** Conflict review page (Medium, mvp)
- **KAN-34** Sources and agent access settings page (Low)

### KAN-10 Epic: Demo, evaluation and documentation
- **KAN-35** Sample company dataset with planted contradictions (~30 docs + small sales DB) (High, mvp)
- **KAN-36** Two-agent demo over MCP: Sales agent + Finance agent (High, mvp)
- **KAN-37** Evaluation: extraction, entity resolution, conflict detection accuracy (Medium)
- **KAN-38** README, architecture docs and quick start (Medium, mvp)

### Build order

Setup (KAN-4) → Ontology (KAN-5) → Ingestion (KAN-6) → Knowledge graph (KAN-7) → MCP (KAN-8) → UI (KAN-9) → Demo and docs (KAN-10)

**Suggested first coding steps:** KAN-11 (repo), KAN-12 (Docker), KAN-14 (core ontology), KAN-19 (Docling parsing), KAN-23 (Graphiti), KAN-27 (MCP read tools). This gives an end-to-end slice early.

---

## 18. Demo plan (5 minutes)

1. Load ~30 sample company documents (PDF, Word, Excel, PowerPoint) and a small sales database.
2. Ask an agent: "Who is our biggest customer and who manages them?" Show the answer with sources.
3. Add a new document that contradicts an old one.
4. `get_conflicts` flags it; the answer updates and cites both sources.
5. A Sales agent and a Finance agent each see only their own business function's data.
6. Agent B reads a fact that Agent A wrote back.

---

## 19. Evaluation and success metrics

- Extraction precision and recall on a hand-labelled set (target 0.8+)
- Entity resolution accuracy on duplicate names
- Conflict detection: share of planted contradictions found
- Answer grounding: share of agent answers with a valid source
- Query latency under 1 second on demo data
- Time to onboard a new data source

---

## 20. Scope

**MVP includes:** core ontology + retail pack; folder and PostgreSQL connectors; Docling parsing; LLM extraction; entity resolution; contradiction handling; MCP server with scoped access; simple UI; demo dataset and two-agent demo.

**Not in MVP:** Gmail and Drive connectors; OCR/audio/video; multi-tenant hosting; scale beyond a few thousand docs; fine-tuned extraction models.

**Roadmap:**
- Next: Gmail and Drive connectors, Omnigraph and TrustGraph backend adapters, per-user access rules, more industry packs
- Later: scale to 50k+ docs (classify first, cheaper models, batching, caching), auto-ontology from databases, graph explorer UI improvements, accuracy benchmarks

---

## 21. Risks and mitigations

| Risk | Mitigation |
|---|---|
| LLM extraction errors | Ontology-constrained output, confidence scores, human review in UI |
| Cost at 50k docs | Classify first, cheaper models, batch and cache |
| Wrong entity merges | Conservative thresholds, reversible merges |
| Privacy of company data | Self-hosted, scoped access, audit log |
| Ontology too generic | Industry packs extend the core |
| Library API changes | Pin versions, check current docs before integrating |

---

## 22. Likely questions from the instructor

- **"Isn't this already built?"** Parts exist (TrustGraph, Graphiti, Snowflake/Databricks ontologies, Palantir). Keystone combines open-source pieces into one layer with a standard cross-industry ontology, a connector SDK, contradiction handling and scoped agent access. Snowflake/Databricks/Palantir are closed and platform-locked; Keystone is open and self-hosted.
- **"Why not just give the documents to ChatGPT?"** It can't tell which fact is current, where a fact came from, or who is allowed to see it.
- **"How does it handle 50,000 docs?"** Ingest once into the graph; insights come from graph queries, not re-reading documents.

---

## 23. Instructions for Claude Code

- Work through Jira tasks in the build order above; reference the KAN key in commit messages (e.g. `KAN-11: create repo structure`).
- Reuse the open-source libraries listed. Do not reimplement what Graphiti, Docling or the MCP SDK already do.
- Check current library docs and versions before writing integration code.
- Follow the Palantir-style ontology principles in section 8.
- Every stored fact must keep source, date and business function.
- Scope filtering happens before results are returned to an agent, never after.
- Keep the MVP small and working end to end before adding features.
- Write tests for conflict resolution rules and scope filtering.
