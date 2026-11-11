"""Tool-execution contracts for later agents (coding agent, computer control).

V1 ships the contracts only. No tool is registered and nothing here executes
anything. See ``contracts.py`` for the binding rules.
"""

from .contracts import (  # noqa: F401
    Capability,
    CapabilityGrant,
    ToolAuditEntry,
    ToolCall,
    ToolExecutor,
    ToolRegistry,
    ToolResult,
    ToolSpec,
    current_host_id,
)
