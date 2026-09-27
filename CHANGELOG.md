# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The published clients (`spaider-cli`, `spaider-client`) version independently of
the core backend; see each package's own metadata and `sdk/python/CHANGELOG.md`
for their released versions. The release process is documented in
[RELEASING.md](RELEASING.md).

## [Unreleased]

## [0.3.1] - 2026-09-27

Security patch. Closes broken access control and namespace-isolation gaps on
the REST API. These only affect deployments that run with
`REQUIRE_API_KEY_AUTH=true` (multi-tenant / network-exposed); single-tenant
local deployments with auth off are unchanged. Upgrading is strongly
recommended for any shared deployment.

### Security
- Agent management routes now enforce authentication and ownership. Listing all
  agents, rotating an agent's API key, deleting an agent (and its graph),
  updating an agent, exporting/importing a graph, wiping episodic memory, and
  every per-agent config toggle previously ran with no auth even when
  `REQUIRE_API_KEY_AUTH=true`. They now require a valid key, and a non-admin key
  may act only on its own namespace (admins may act across namespaces).
- Raw Cypher (`POST /query/cypher`) is now admin-only and runs inside a Neo4j
  read transaction. A non-admin key can no longer read another agent's subgraph
  by embedding its UUID in a raw query, and writes smuggled past the keyword
  filter (for example via APOC procedures) are rejected by the database rather
  than executed. The write-keyword filter also now blocks `CALL` and `LOAD CSV`.
- Graph traversal (`POST /query/traverse`) is scoped to the caller's namespace,
  closing the previously acknowledged IDOR on `start_node_id`.
- Node deletion (`DELETE /nodes/{id}`) no longer trusts the spoofable
  `X-Agent-Permission` header. It requires an admin API key, and the audit entry
  records the authenticated identity.
- Ingestion, feedback, and global system-settings routes now require
  authentication; write routes are scoped to the caller's namespace so a valid
  key cannot poison another agent's graph or synaptic weights.
- Deleting an agent now revokes all of its API keys, removing dangling
  credentials that could still authenticate against an orphaned namespace.
- Privilege escalation via agent creation/update is closed: only an admin key
  may grant the `admin` permission. The first admin must be provisioned
  out-of-band (`backend/scripts/bootstrap_admin.py`).
- The backend logs a loud warning at startup when `REQUIRE_API_KEY_AUTH` is off.

### Known follow-ups
- Read-only cross-namespace exposure on some analytics/replay/graph/synthesize
  routes is tracked for a subsequent patch; these require a valid key and only
  matter in multi-tenant mode.

## [0.3.0] - 2026-07-04

Vision: agents can now remember what is in images. Plus contradiction-safety
robustness and MCP 2.x compatibility.

### Added
- Vision ingest. A vision-capable model reads an image (documents, charts,
  diagrams, screenshots, photos) into a text knowledge graph, then the same
  resolve, embed, write and retrieve pipeline as text runs unchanged. New
  `POST /ingest/image` endpoint and an Image tab in the Studio ingest panel
  (drop or browse, live preview, animated graph pop-in). No image embeddings or
  new index: the image becomes text nodes, so retrieval is cross-modal by
  construction.

### Changed
- MCP server migrated to the mcp 2.x SDK. The `@server.list_tools()` /
  `@server.call_tool()` decorators removed in mcp 2.0 are replaced by
  constructor callbacks; the `mcp` dependency is unpinned to `>=2.0.0,<3.0.0`.
  The Streamable HTTP transport is unchanged.

### Fixed
- Per-agent config toggles (Synaptic Memory, Hibernation cadence, Supersession)
  no longer fail on an agent that lacks a SystemAgent node: the node is now
  created on demand (MERGE) instead of returning 404.
- The backend (`/health`, FastAPI) and the Studio Settings page now report the
  correct version.

## [0.2.0] - 2026-07-03

Cognitive memory tiers: every agent can be configured as a long-term archive or
a consolidating working memory, with contradiction resolution and measured
continual learning.

### Added
- Per-agent memory tiers. Three independent switches on each agent card:
  Synaptic Memory (Hebbian reinforcement plus decay on the graph edges),
  Supersede updates (Archive keeps the full history, Working keeps only the
  current state), and Hibernation cadence (scheduled consolidation). New
  endpoints `GET/POST /agents/{id}/supersession` join the existing memory-mode
  and consolidation controls.
- Ingest-time contradiction and update resolution (supersession). A current
  state fact that updates a functional attribute (a new CEO, a moved
  headquarters) supersedes the prior fact, so retrieval returns the current
  value instead of both. A state-versus-event gate leaves events untouched: it
  fires zero false updates on a real 83-fact business corpus while lifting
  current-state accuracy from 0 to 100% on genuine updates (95% CI excludes 0).
  Off by default (Archive).
- Continual-learning benchmarks. GEM-style forgetting and knowledge-transfer
  metrics with a task-sequence runner and bootstrapped confidence intervals, so
  the memory system's retention and transfer are measurable.

### Changed
- Benchmarks reorganized into `benchmarks/continual/` (task sequences, runner,
  metrics) and `benchmarks/reports/` (comparison scorecards); continual-learning
  models moved from dataclasses to pydantic.

## [0.1.0] - 2026-06-17

Initial public release.

### Added
- MCP-native knowledge-graph memory backend (FastAPI, Neo4j, Kafka, Redis,
  Postgres, ClickHouse): ingest unstructured text, extract entities and
  relationships, answer questions grounded in the graph.
- Hybrid retrieval: dense vector search plus keyword full-text search fused
  with reciprocal-rank fusion, an agentic retrieve-verify loop, and concise
  direct answers for factoid questions.
- Cognitive memory layer: ACT-R energy decay with retrieval-based
  consolidation, Hebbian edge feedback (`spaider.feedback`), and scheduled
  graph consolidation (orphan pruning, duplicate fusion).
- Verbatim fact preservation: every ingested text is kept as a FACT node,
  including texts the extractor finds no entities in, so literal values are
  never lost.
- `spaider` CLI: one-command `init` wizard (Docker check, provider key
  validation, secret generation, stack startup, agent provisioning, MCP +
  skill install; re-run safe), `doctor` health audit, and agent management.
- `spaider-client` Python SDK with sync/async clients, a typed OpenAPI
  contract guard, and LangChain/LlamaIndex integrations.
- Studio web UI: 3D knowledge-graph canvas, cross-agent Multiverse view,
  agent management, training-data export, and a ClickHouse-backed audit log
  of every ingest and query.
- Training-data export: ChatML (SFT) streaming export and DPO preference
  pairs labeled by the graph's own usage signal (RLHG), via UI, REST, and CLI.
- Connector framework (upload, URL, SQL, MCP) with incremental sync state and
  a Kafka dead-letter queue.
- Public vanilla-vs-with-memory benchmark suite: independent LLM judge,
  multi-sweep runs, bootstrapped confidence intervals, published scorecards.
- CI/CD: consolidated path-filtered CI workflow, grouped release workflows
  with PyPI trusted publishing (OIDC) and GHCR container images, weekly
  dependabot with majors isolated, coverage reporting to Codecov.

[Unreleased]: https://github.com/Spaider-studio/spaider/compare/v0.1.0...HEAD
[0.1.0]: https://github.com/Spaider-studio/spaider/releases/tag/v0.1.0
