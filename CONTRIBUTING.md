# Contributing to SpAIder

Thanks for considering a contribution. SpAIder is open source: the core server and frontend under the **GNU AGPL-3.0**, and the client CLI + Python SDK under **Apache-2.0** (see [License](README.md#license)); we welcome bug reports, feature ideas, documentation improvements, and code contributions.

## Quick start

1. Fork the repo and clone your fork.
2. Bring up the dev stack with `make dev`. Run `make test` to confirm a green baseline before editing.
3. Branch off `main`. Suggested convention: `feat/<topic>`, `fix/<topic>`, `docs/<topic>`, `chore/<topic>`.
4. Open a PR using the template (`.github/pull_request_template.md`).
5. Sign the CLA when the bot prompts you (see below).

## Contributor License Agreement (CLA)

**All contributors must sign the SpAIder Contributor License Agreement before their first PR can be merged.**

We use [CLA Assistant](https://cla-assistant.io/) to manage this; a bot will automatically comment on your first pull request with a one-click sign-in flow. After you sign once, all future PRs from your account auto-pass.

**Why we have a CLA**: the open-source licenses grant the public a permanent license to use SpAIder. The CLA grants the project maintainers an additional, separate license to redistribute your contribution under other terms, for example the **commercial / Enterprise license** offered alongside the AGPL core, or a relicense if the project's needs change. The publicly-released code stays open source (AGPL-3.0 core, Apache-2.0 clients); the CLA just preserves the maintainers' optionality. Same construct used by MongoDB, HashiCorp, and many others.

You retain your copyright. Read the [Apache Individual CLA](https://www.apache.org/licenses/icla.pdf) (the text our CLA mirrors) for the full mechanics.

If you're contributing on behalf of a company, your employer may need to sign the [Corporate CLA](https://www.apache.org/licenses/cla-corporate.pdf) variant. The CLA Assistant flow handles both.

## What we're looking for

**Particularly welcome**:
- Bug fixes with a reproducing test case
- Connector implementations for new data sources (see `backend/app/connectors/` for the abstract base class)
- Documentation improvements, especially for setup/quickstart
- Performance benchmarks and regressions (the `benchmarks/` suite is designed for exactly this)
- Honest negative results. We treat "we tried this and it didn't work" as first-class data; include the experiment design and what you measured

**Discuss before opening a large PR**:
- Schema or storage changes (Neo4j label additions, new Kafka topics, new Redis key prefixes)
- Changes to the `BaseConnector` ABC or `RunState` schema in `backend/app/connectors/__init__.py`
- Edits to authentication / authorization paths in `backend/app/services/auth_service.py`
- Prompt edits in `backend/app/services/compressor.py`, `query_service.py`, or `entity_resolver.py`

These are stop-and-ask triggers: open an issue first to align before investing in the implementation.

## Code style

- Python: follow `make format` (Ruff) and `make lint` (Ruff + mypy where configured).
- TypeScript / React: follow `npm run lint` in `frontend/`.
- **PR titles must follow Conventional Commits** (`feat:`, `fix:`, `docs:`, `chore:`/`ci:`/`refactor:`/`perf:`/`test:`, optional scope like `feat(cli):`, and a `!` for breaking changes, e.g. `feat(api)!:`). The PR Labeler turns the prefix into a release-notes category, and since we squash-merge, the title becomes the commit on main. Individual commits inside a PR can be messy; the title cannot. Branch names are free-form.

## Versioning policy (SemVer)

Spaider follows [Semantic Versioning 2.0.0](https://semver.org/). The mechanics of cutting a release (tags, automation, changelog flow) are documented in [RELEASING.md](RELEASING.md). Each releasable artifact (`backend`, `spaider-cli`, `spaider-client` SDK, frontend) versions independently; see each subproject's `pyproject.toml` / `package.json`.

Reading a version `MAJOR.MINOR.PATCH`:

- **PATCH** (`0.1.0` → `0.1.1`): bug fixes only. Existing API surface unchanged. Safe to upgrade automatically.
- **MINOR** (`0.1.0` → `0.2.0`): new functionality, backwards-compatible. Existing callers keep working without changes.
- **MAJOR** (`0.1.0` → `1.0.0`, or any breaking change pre-1.0): backwards-incompatible. Removed/renamed identifiers, changed method signatures, changed HTTP response shapes, dropped supported Python versions, license change, etc. CHANGELOG entry must call out exactly what breaks and the migration path.

### Pre-1.0 special case

While any artifact is on `0.y.z`, the **MINOR** bump may also include breaking changes. That's the SemVer spec's explicit allowance for projects under active development. We try to minimise this in practice: if you can avoid the break, do; if you can't, document it loudly in the CHANGELOG `### Breaking changes` section.

A `1.0.0` release signals that the public API surface is stable enough that we commit to MAJOR bumps for any future break. Each subproject hits 1.0 on its own schedule.

### What counts as the "public API"

- **`spaider-client`**: every name exported from `spaider.__init__` and `spaider.integrations.*`; the HTTP shape the client sends and the shape it expects back. Internal helpers (anything with a leading `_`, or under `spaider._internal/`) are not covered.
- **Backend REST API**: every documented endpoint at `/api/v1/*`. Endpoints under `/api/v2alpha/` or marked "experimental" in the response are explicitly excluded.
- **MCP tool surface**: tool names + their `inputSchema` JSON Schema. Adding optional fields is MINOR; renaming or removing fields is MAJOR.
- **CLI commands** (`spaider-cli`): the documented commands and their flags. `--help` output is the contract.

### Deprecation

When a feature is going to be removed, mark it with a `DeprecationWarning` (Python) or a console warning (CLI) in the **MINOR** release before, then remove in the next MAJOR. CHANGELOG `### Deprecated` section lists what's on the chopping block.

## Tests

- New behaviour needs at least one happy-path test and one error/edge path test.
- Integration tests should hit real services (Neo4j, Redis, Kafka) via the docker-compose dev stack, not mocks. Mocks routinely pass while the real wire contract breaks; we burned too much time on that to keep doing it.
- Run the full suite (`make test`) before opening the PR.

## Code of conduct

This project follows the [Contributor Covenant 2.1](./CODE_OF_CONDUCT.md). All interactions on issues, PRs, Discussions, and any other community channel are expected to follow it.

## Reporting security issues

Don't open a public issue for security vulnerabilities. See [`SECURITY.md`](./SECURITY.md) for the disclosure process.

## Questions

- General product questions, design discussions: open a [GitHub Discussion](https://github.com/Spaider-studio/spaider/discussions).
- Bug reports / feature requests: open a GitHub Issue using the templates in `.github/ISSUE_TEMPLATE/`.

We try to respond to issues within a few days. We're a small part-time team, so bear with us.
