"""Shared utilities for SpAIder CLI subcommands.

Will house in the v0.1.0 release:

- ``docker``: detect Docker daemon, start stack, wait for healthchecks.
- ``env``: read / write / merge .env files; generate secrets.
- ``api``: SpAIder REST client (agents, health).
- ``llm``: provider key validation via small test calls.
- ``mcp``: ``~/.claude/.mcp.json`` and skill-file management.
"""
