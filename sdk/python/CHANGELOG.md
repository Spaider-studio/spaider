# Changelog

All notable changes to `spaider-client` are documented here.

The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## [0.1.0] - 2026-05-23

Initial public release.

### Added

- Synchronous `Spaider` client and asynchronous `AsyncSpaider` client over `httpx`.
- Knowledge-graph methods: `ingest`, `query`, `traverse`, `get_graph`, `get_node`, `delete_node`.
- Fine-tuning dataset synthesis via `synthesize`.
- Swarm queries across multiple agents: `create_swarm_connection`, `swarm_query`.
- Typed exception hierarchy: `SpaiderError`, `AuthError`, `NotFoundError`, `RateLimitError`, `ValidationError`, `ServerError`.
- LangChain memory integration: `spaider.integrations.langchain.SpaiderMemory`.
- LlamaIndex integration: `spaider.integrations.llamaindex.SpaiderIndex` and `SpaiderQueryEngine`.
- PEP 561 `py.typed` marker so downstream type checkers pick up the inline type hints.

[Unreleased]: https://github.com/Spaider-studio/spaider/compare/sdk-python/v0.1.0...HEAD
[0.1.0]: https://github.com/Spaider-studio/spaider/releases/tag/sdk-python/v0.1.0
