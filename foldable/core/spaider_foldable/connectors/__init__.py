"""Memory connectors (ADR-6): the SpAIder memory of one profile, exposed to
external AI applications through MCP.

  service.py   grants (connector records with hashed tokens, projects, rights),
               the tool implementations (status, search_memory, get_source,
               remember, correct_memory, forget_memory) and the setup snippets
               for Claude Desktop, Claude Code, Codex and generic clients
  bridge.py    the stdio MCP server (``spaider-foldable-core.exe --mcp-stdio``)
               that forwards tool calls to the RUNNING core over loopback HTTP
               (the core stays the only SQLite writer)
"""
