# Writing a Keystone connector

A connector brings data from one system (a CRM, a ticketing tool, a mailbox, a database) into
Keystone. You write **one Python class with three methods**; Keystone handles parsing, ontology
extraction, contradiction handling and agent access. Expect about an hour for a simple API.

## How it fits

```
your system ──► YourConnector.read() ──► Document ──► Docling parsing ──► ontology extraction ──► graph
```

Every connector returns the same `Document` model, so nothing after `read()` needs to know
where the data came from.

| Method | Question | Returns |
|---|---|---|
| `check()` | Can I connect with this configuration? | `CheckResult` (report problems, don't raise) |
| `discover()` | What collections are there (folders, tables, object types)? | `list[Stream]` |
| `read(stream=None, since=None)` | Give me the items | `Iterator[Document]` |

## 1. Generate a project from the template

```bash
uvx cookiecutter gh:alishbas22/Keystone --directory templates/connector
```

You are asked for a name (e.g. `acme-crm`), a display name, a description and the business
function the data belongs to. The result is a working project:

```
keystone-acme-crm/
├── pyproject.toml                 # registers the connector (entry point)
├── src/keystone_acme_crm/connector.py
├── sample_data/records.json       # example data so it runs immediately
└── tests/test_connector.py        # Keystone contract tests
```

Run it straight away:

```bash
cd keystone-acme-crm
uv sync
uv run pytest
```

## 2. Connect your system

Open `connector.py`:

1. **Config:** replace the fields of the `...Config` model with what your system needs (base URL,
   API key as `SecretStr`, workspace id). Keystone renders this model as a settings form.
2. **Fetching:** replace `_fetch_records()` with calls to your system's API or SDK.
3. **Documents:** in `read()`, build one `Document` per item:

| Field | Use |
|---|---|
| `id` | Stable id within your system (e.g. `tickets/4711`). Re-syncs update instead of duplicating. |
| `source` | Always `self.source_name`. |
| `title` | Human-readable title. |
| `text` **or** `content` + `file_name` | Text you already have, or raw file bytes (`file_name` picks the parser: `.pdf`, `.docx`, `.xlsx`, `.pptx`, `.eml`, `.md`, `.html`, `.csv`). |
| `modified_at` | Timezone-aware time of the last change. Used for incremental sync and for dating facts. |
| `author`, `url` | Who wrote it and where a human can open it (shown as provenance). |
| `business_function` | `Sales`, `Finance`, `HR` or `Operations`. Decides which agents may see it. |

Tips:

- Write facts plainly in `text` (`"Sara Khan is the account manager for Harbor Hotels Group."`).
  Clear statements give better extraction than raw JSON.
- Support `since` if your system can filter by update time, and set
  `supports_incremental=True` in the manifest.
- Never put secrets in documents or log messages.

## 3. Test against the contract

`keystone.connectors.testing.check_connector` runs `check()`, `discover()` and `read()` and
verifies the rules: unique and stable ids, correct `source`, valid business functions,
timezone-aware dates. The template's tests already call it.

## 4. Install it into Keystone

The entry point in `pyproject.toml` registers the connector:

```toml
[project.entry-points."keystone.connectors"]
acme-crm = "keystone_acme_crm:AcmeCRMConnector"
```

The name on the left must equal `manifest.name`. Install the package into Keystone's
environment and check that it is found:

```bash
uv pip install -e ../keystone-acme-crm
uv run python -m keystone.connectors
```

A connector that fails to load is listed as `BROKEN` with the reason; it never stops Keystone.

## Built-in connectors

| Name | Reads |
|---|---|
| `folder` | Supported files in a folder tree. A subfolder named `Finance`, `Sales`, `HR` or `Operations` sets the business function. |
| `sql` | PostgreSQL or any SQLAlchemy database. Proposes a table-to-ontology mapping you review (see below). |
| `google-drive` | Google Drive files and Google Docs, Sheets and Slides (read-only). Setup: [google.md](google.md). |
| `gmail` | Gmail messages and supported attachments (read-only). Setup: [google.md](google.md). |

### Mapping a database

```bash
uv run python -m keystone.connectors.sql propose "postgresql+psycopg://user:pw@host/db" \
    --owner "Your Company Ltd" --out mapping.yaml
```

The proposal maps tables to entity types (`customers` → Organization), link tables to
relations (`order_items` → `contains_product`), foreign keys to named relations
(`account_manager_id` → `manages_account`) and adds `customer_of` links to your company. Review
the file, fix anything wrong, set `reviewed: true` and pass it as `mapping_file`. Keystone
validates the mapping against the ontology and the database before reading.
