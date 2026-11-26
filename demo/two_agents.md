# Two-agent demo (KAN-36)

A **Sales agent** and a **Finance agent** share one Keystone over MCP. Each has its own key, sees
only its own business functions, and both can read and write a shared function (Operations).

| Agent | Key gives access to |
|---|---|
| `demo-sales` | Sales, Operations |
| `demo-finance` | Finance, Operations |

## Run it

```bash
docker compose up -d neo4j              # graph database
uv run python demo/ask.py --load        # load the sample company (core demo, 5 documents)
uv run python demo/two_agents.py        # start an MCP server and play both agents
```

`two_agents.py` creates the two agents (or gives them fresh keys), starts the Keystone MCP server
over HTTP, and makes the tool calls an agent would make. One step (`add_claim`) calls the LLM.

## What you will see

1. **Sales agent: who manages Harbor Hotels Group?** `search_entities` → `get_entity`.
   Bilal Ahmed is *current* (2026 memo); Sara Khan is *superseded* (2025 account plan and Q2
   report). Every fact lists its source documents.
2. **Sales agent: are there contradictions?** `get_conflicts`. The two account managers
   contradict each other (`manages_account` allows one value per customer); the time rule chose
   the newer source.
3. **Finance agent: the same customer.** It sees Harbor's invoice and order, but **no** account
   manager facts: they come from Sales documents and are filtered out inside the database query.
4. **Sales agent writes back** with `add_claim`: *Harbor Hotels Group LOCATED_IN Liverpool*, into
   the shared Operations function. The claim is attributed to `demo-sales`.
5. **Finance agent reads it** with `get_relationships`, including who wrote it. This is shared
   memory between agents.
6. **Audit log.** Every call by each agent is recorded (also on the UI page *Sources & agents*).

Contradiction handling can also be shown live in the UI: upload a document that contradicts an
existing one (or load all 30 sample documents) and open the *Conflicts* page.

## Connect a real AI agent

Create a key for the agent (it is shown once):

```bash
uv run python -m keystone.mcp_server.agents create sales-bot --functions Sales
```

**Over HTTP** (several agents sharing one server):

```bash
uv run python -m keystone.mcp_server --http --port 8765
```

The agent connects to `http://127.0.0.1:8765/mcp` with the header
`Authorization: Bearer <agent key>`.

**Over stdio** (one local agent, e.g. Claude Desktop). Add to the MCP client's configuration:

```json
{
  "mcpServers": {
    "keystone": {
      "command": "uv",
      "args": ["run", "--directory", "C:/path/to/keystone", "python", "-m", "keystone.mcp_server"],
      "env": { "KEYSTONE_AGENT_KEY": "ks_..." }
    }
  }
}
```

The agent then has eight tools: `describe_ontology`, `search_entities`, `get_entity`,
`get_relationships`, `find_path`, `get_conflicts`, `explain_claim` and `add_claim`.

Note: on a Windows machine with antivirus HTTPS/HTTP inspection, the newest MCP protocol version
(2026-07-28) over HTTP may fail; clients using the standard initialize handshake (most clients,
and `mode="legacy"` in the Python SDK) work. Linux CI tests both.
