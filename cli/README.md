# spaider-cli

The official command-line interface for [SpAIder](https://github.com/Spaider-studio/spaider), the memory infrastructure for AI agents.

`spaider-cli` is the recommended way to install, manage, and integrate SpAIder with your AI coding tools (Claude Code, Cursor, and others). It takes you from "never heard of SpAIder" to "working Studio + MCP-integrated Claude Code with a memory skill loaded" in one command.

```bash
pipx install spaider-cli
spaider init
```

## Status

**v0.1.0**. All commands implemented and working.

| Command | Status |
|---|---|
| `spaider --version` / `--help` | ✅ |
| `spaider init` | ✅ One-shot setup wizard |
| `spaider doctor` | ✅ Read-only health audit |
| `spaider agent create / list / rotate-key / delete` | ✅ |
| `spaider mcp install` | ✅ Claude Code + Cursor |

## What it does

1. **`spaider init`**: one command from clone to a working, MCP-integrated stack:
   - Detect Docker (CLI + daemon).
   - Prompt for the LLM provider (OpenAI / Anthropic / Ollama) and **validate the key** with a live probe.
   - Generate the secrets you shouldn't have to type (JWT, connector key, Neo4j password) and write `.env`. **Re-run safe**: existing secrets are preserved, never regenerated, so re-running never locks the stack out of its own database.
   - Start the Docker stack and wait for `/health`.
   - Provision a `dev-${USER}` agent (or `--agent <name>`) and capture its key.
   - Install the MCP server + skill into `~/.claude/` (idempotent merge).

   Flags: `--provider`, `--llm-key`, `--llm-base-url`, `--skip-docker`, `--skip-mcp`, `--agent`.
2. **`spaider doctor`**: read-only audit covering Docker, `.env`, LLM key, backend `/health`, embedding-dimension consistency, and the `~/.claude` MCP + skill wiring. Exit 0 when all green/warn, 1 on a blocking failure.
3. **`spaider agent ...`**: create / list / rotate-key / delete agents against the REST API (resolves agents by name).
4. **`spaider mcp install`**: non-destructively merge SpAIder into your MCP client config (`--for claude-code` | `cursor`, `--scope user` | `project`); also writes the agent-side skill file so LLMs reflexively know when to call SpAIder.

## The skill file

A key part of the value: `spaider mcp install` ships not just the MCP server config but a **skill file** (`~/.claude/skills/spaider.md`) that tells the LLM *when* and *why* to use SpAIder's tools. Without it, MCP tools sit unused. With it, the LLM proactively reaches for SpAIder when the conversation warrants, exactly like `Read`/`Edit`/`Bash` are reflexive in Claude Code today.

Source content is at `src/spaider_cli/skills/claude_code.md` (Apache 2.0); copy and adapt for your own tools if useful.

## Development

```bash
git clone https://github.com/Spaider-studio/spaider.git
cd spaider/cli
pip install -e ".[dev]"
pytest
```

Or from the SpAIder monorepo root:

```bash
make cli-dev    # editable install of the CLI
make cli-test   # run cli/tests/
```

## Release (maintainers only)

CI runs as the `cli-tests` job of `.github/workflows/ci.yml` (every PR + push to main that touches `cli/`); releases run from `.github/workflows/cli-release.yml` on tag push.

One-time setup before the first PyPI release:

1. Create a [PyPI account](https://pypi.org/account/register/) for the project.
2. Register a Trusted Publisher (pending publisher) for `spaider-cli`:

   - PyPI Project → Publishing → Add a new pending publisher.
   - Owner: `Spaider-studio`, repo: `spaider`, workflow: `cli-release.yml`, environment: `pypi`.
   - GitHub: Settings → Environments → Create environment named `pypi` (no protection rules needed for the first release).

3. Tag and push (CLI releases use namespaced `cli/v*` tags; plain `v*` tags release the container images instead):

   ```bash
   git tag cli/v0.1.0
   git push origin cli/v0.1.0
   ```

   The workflow builds sdist + wheel, runs `twine check`, then publishes via the OIDC trusted-publisher flow (no API tokens stored in the repo).

To rehearse without publishing, use the manual `workflow_dispatch` trigger with `dry_run: true`: it builds the artifacts but skips PyPI.

## License

Apache 2.0. See [LICENSE](https://github.com/Spaider-studio/spaider/blob/main/LICENSE).
