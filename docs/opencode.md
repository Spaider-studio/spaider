# Using SpAIder with OpenCode

[OpenCode](https://opencode.ai) is an open-source coding agent that runs on any
model, including local [Ollama](https://ollama.com) models, with no API key required.
SpAIder plugs in as an MCP server, giving OpenCode durable, queryable memory
across sessions. Together they make a **100% self-hosted agent + memory stack**.

## 1. Bring up SpAIder

Run the stack as usual (see the main [README](../README.md)). SpAIder serves its
MCP endpoint over **Streamable HTTP** at `http://localhost:8000/api/v1/mcp`,
authenticated with a per-agent bearer token.

## 2. Wire SpAIder into OpenCode

One command writes the OpenCode config and an `AGENTS.md` guidance block:

```bash
spaider mcp install --for opencode
```

This:

- mints (or rotates) a `dev-$USER` agent key via the API,
- writes an `mcp.spaider` entry into `~/.config/opencode/opencode.json`
  (use `--scope project` to write `./opencode.json` for the current repo only),
- appends a SpAIder guidance section to `AGENTS.md` so OpenCode knows when to
  reach for the `spaider.*` tools.

The resulting `opencode.json` looks like:

```json
{
  "mcp": {
    "spaider": {
      "type": "remote",
      "url": "http://localhost:8000/api/v1/mcp",
      "enabled": true,
      "headers": { "Authorization": "Bearer sk-..." }
    }
  }
}
```

> `opencode.json` (and `./.mcp.json` for project-scoped Claude Code) holds a
> secret bearer token. Add it to `.gitignore` if you write it into a repo.

Restart OpenCode; it will list and call `spaider.query`, `spaider.ingest_fact`,
`spaider.list_recent`, and `spaider.feedback`.

## 3. (Optional) Run everything on local models

Point both SpAIder and OpenCode at Ollama for a fully offline setup.

**SpAIder**: the shipped `.env.example` already defaults to Ollama for the LLM
**and** embeddings:

```bash
ollama pull qwen2.5:3b           # LLM
ollama pull embeddinggemma:300m  # embeddings (768-dim)
```

**OpenCode**: select an Ollama model in its config (see the
[OpenCode models docs](https://opencode.ai/docs/models/)); it connects to
`http://localhost:11434` with no key.

### Embedding dimensions

`EMBEDDING_DIMENSIONS` must match your embedder and be set **before the first
ingest**, since it sizes the Neo4j vector index:

| Embedder | Dimensions |
|---|---|
| Ollama `embeddinggemma:300m` | 768 |
| OpenAI `text-embedding-3-small` | 1536 |
| `all-MiniLM-L6-v2` | 384 |

Switching embedders on a graph that already has vectors corrupts the index;
rotate the agent (or re-seed) when you change. The backend logs a startup error
if it detects a dimension mismatch.

## Verify

`spaider doctor` reports whether the OpenCode config carries the SpAIder entry,
alongside the Claude Code checks.
