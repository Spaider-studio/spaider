# Changelog

All notable changes to this project are documented here.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project follows [Semantic Versioning](https://semver.org/spec/v2.0.0.html).
The published clients (`spaider-cli`, `spaider-client`) version independently of
the core backend; see each package's own metadata and `sdk/python/CHANGELOG.md`
for their released versions. The release process is documented in
[RELEASING.md](RELEASING.md).

## [Unreleased]

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
