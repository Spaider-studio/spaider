# Installation

```bash
git clone https://github.com/Spaider-studio/spaider.git
cd spaider
pip install -e ./cli         # or pipx install ./cli (recommended)
spaider init                 # interactive setup wizard
```

That's the whole flow. `spaider init` checks Docker, prompts for your LLM provider (OpenAI / Anthropic / Ollama) and validates the credentials (Ollama needs none), generates the JWT/connector/Neo4j secrets, writes `.env`, brings up `backend-api`, provisions a personal `dev-<USER>` agent, and writes `~/.claude/.mcp.json` + `~/.claude/skills/spaider.md` so Claude Code reflexively reaches for SpAIder. Restart Claude Code and you're done. (Re-running is safe: existing secrets are preserved.)

Run `spaider doctor` any time for a read-only audit of the install.

**Prefer prebuilt images?** Skip the source build , pull the published containers and run the stack:

```bash
git clone https://github.com/Spaider-studio/spaider.git && cd spaider
cp .env.example .env          # defaults to local Ollama (LLM + embeddings, no key)
docker compose pull           # fetch ghcr.io/spaider-studio/spaider-* images
docker compose up -d          # latest by default; pin with SPAIDER_VERSION=<x.y.z>
```

The default compose builds the app images from source (`docker compose up --build`); the lines above pull the prebuilt images from GHCR instead.

## Wire it into your coding agent

`spaider init` wires Claude Code automatically. For other MCP clients, one command writes the config (Streamable HTTP endpoint + your agent's bearer token):

```bash
spaider mcp install --for claude-code   # ~/.claude/.mcp.json  (default)
spaider mcp install --for opencode      # ~/.config/opencode/opencode.json + AGENTS.md
spaider mcp install --for cursor        # ./.cursorrules
```

All three point at the same backend (`http://localhost:8000/api/v1/mcp`); each agent then sees the `spaider.*` tools. `spaider doctor` reports which agents are wired up.

## Run fully local

SpAIder is provider-agnostic end to end. **No model step is pinned to a proprietary API.** The shipped `.env.example` defaults to local [Ollama](https://ollama.com) for **both** the LLM and embeddings, so the whole stack runs offline with no API key:

```bash
ollama pull qwen3.5:9b           # LLM (or gemma4:e4b)
ollama pull embeddinggemma:300m  # embeddings (768-dim)
cp .env.example .env             # already defaults to the two models above
docker compose up -d
```

Pair it with [OpenCode](https://opencode.ai) (which also runs on Ollama) for a 100% self-hosted agent + memory stack. Switch any step to OpenAI/Anthropic by uncommenting the cloud block in `.env`. One caveat: `EMBEDDING_DIMENSIONS` must match your embedder (Ollama `embeddinggemma` = 768, OpenAI `text-embedding-3-small` = 1536) and be set **before the first ingest** (it sizes the Neo4j vector index). See [docs/opencode.md](opencode.md) for the OpenCode walkthrough.

<details>
<summary><b>Manual install</b> (advanced)</summary>

```bash
git clone https://github.com/Spaider-studio/spaider.git
cd spaider
make setup                   # copies .env.example → .env
```

Edit `.env` and fill in your secrets:

```bash
LLM_API_KEY=sk-your-key-here
NEO4J_PASSWORD=choose-a-strong-password
SPAIDER_BOOTSTRAP_TOKEN=$(python -c "import secrets; print(secrets.token_hex(32))")
CONNECTOR_SECRET_KEY=$(python -c "import secrets,base64; print(base64.b64encode(secrets.token_bytes(32)).decode())")
```

Bring up the stack and wire MCP by hand:

```bash
make dev          # development with hot reload   (make prod for production)
scripts/dev/setup_mcp_dev_agent.sh
# copy the printed JSON block into ~/.claude/.mcp.json under mcpServers
```

(`spaider init` does all of the above in one command.)

</details>

## Running only Neo4j and the API

Set `COMPOSE_PROFILES=lite` in `.env` for the Lite profile. Key-value, job and audit features then run in-process and in Neo4j, so Kafka, Redis, Postgres and ClickHouse are not needed.

## Use SpAIder in Claude Code

Once `spaider init` completes:

1. Restart Claude Code so it picks up `~/.claude/.mcp.json` and the new `~/.claude/skills/spaider.md` skill.
2. In a fresh session, ask Claude *"what do you remember about this project?"*. It should reach for `spaider.list_recent` and `spaider.query` on its own.
3. Capture non-obvious learnings via `spaider.ingest_fact`. The skill file documents when each tool is appropriate, the feedback protocol (`spaider.feedback`), and how to fail gracefully when the backend is down.

The skill file is the high-leverage half. Without it Claude Code sees `spaider.*` as just-another-tool; with it the LLM has explicit triggers for when to use each one. Source: [`cli/src/spaider_cli/skills/claude_code.md`](../cli/src/spaider_cli/skills/claude_code.md), free to copy and adapt for other AI tools.

## Python SDK

```bash
pip install spaider-client
```

```python
from spaider import Spaider

sp = Spaider(api_key="sk-...", agent_id="my-agent")
sp.ingest("Max Mustermann arbeitet bei Google.")
answer = sp.query("Wo arbeitet Max?")
print(answer.text, answer.subgraph.nodes)

# Fine-tuning dataset export
sp.synthesize(strategy="reasoning", max_samples=1000).save("training.jsonl")
```

An async client (`AsyncSpaider`) and **LangChain** / **LlamaIndex** memory adapters ship in the same package. See [`sdk/python/README.md`](../sdk/python/README.md) for full SDK documentation.
