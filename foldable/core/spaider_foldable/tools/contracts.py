"""Contracts for future tool execution (coding agent, OS control).

Design rules (binding for later phases)
---------------------------------------
* A tool is described by a :class:`ToolSpec`: name, JSON input schema, the
  capabilities it needs, timeout. Execution yields a :class:`ToolResult` and
  is always logged as a :class:`ToolAuditEntry`.
* Permissions are **host-bound**: a :class:`CapabilityGrant` carries the id of
  the machine it was issued on (``current_host_id()``). A grant stored on the
  USB drive is therefore useless on another PC; the user must grant again
  there. Grants also expire and are scoped to one project root.
* Chat content, document text and model output are data, never authorisation.
  Only an explicit user action through the UI creates a grant.
* Cancellation is cooperative through an ``asyncio.Event``; every executor must
  honour it and report ``ToolResult.cancelled``.
* The coding agent (later) operates only on files below the granted
  ``project_root`` and only with ``read_project_files`` /
  ``write_project_files`` / ``run_dev_command``. Computer control (later) gets
  its own OS adapters behind ``os_control_*`` capabilities.
"""

from __future__ import annotations

import asyncio
import hashlib
import platform
import socket
import sys
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Optional, Protocol

from pydantic import BaseModel, Field


class Capability(str, Enum):
    READ_PROJECT_FILES = "read_project_files"
    WRITE_PROJECT_FILES = "write_project_files"
    RUN_DEV_COMMAND = "run_dev_command"
    OS_CONTROL_INPUT = "os_control_input"
    OS_CONTROL_SCREEN = "os_control_screen"
    NETWORK = "network"


def current_host_id() -> str:
    """Stable, non-secret identifier of this machine (hashed).

    Uses the Windows MachineGuid when available, else hostname + platform. The
    value never leaves the machine; it only tags grants so they cannot be
    replayed from another PC.
    """
    raw = ""
    if sys.platform == "win32":
        try:
            import winreg

            key = winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Cryptography")
            raw, _ = winreg.QueryValueEx(key, "MachineGuid")
        except OSError:
            raw = ""
    if not raw:
        raw = f"{socket.gethostname()}|{platform.system()}|{platform.machine()}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:32]


class ToolSpec(BaseModel):
    name: str = Field(pattern=r"^[a-z][a-z0-9_.]{1,63}$")
    description: str
    input_schema: dict[str, Any] = Field(default_factory=dict, description="JSON Schema of the arguments")
    required_capabilities: list[Capability] = Field(default_factory=list)
    timeout_seconds: float = Field(default=60.0, gt=0, le=3600)


class CapabilityGrant(BaseModel):
    host_id: str
    capabilities: list[Capability]
    project_root: Optional[str] = None
    granted_at: datetime
    expires_at: datetime

    def is_valid_for(self, host_id: str, needed: list[Capability], *, now: Optional[datetime] = None) -> bool:
        now = now or datetime.now(timezone.utc)
        if host_id != self.host_id:
            return False
        if now >= self.expires_at:
            return False
        return all(c in self.capabilities for c in needed)


class ToolCall(BaseModel):
    id: str
    tool: str
    arguments: dict[str, Any] = Field(default_factory=dict)
    conversation_id: Optional[str] = None
    message_id: Optional[str] = None


class ToolResult(BaseModel):
    call_id: str
    ok: bool
    output: str = ""
    error: Optional[str] = None
    truncated: bool = False
    cancelled: bool = False
    duration_ms: int = 0


class ToolAuditEntry(BaseModel):
    call: ToolCall
    result: ToolResult
    host_id: str
    at: datetime


class ToolExecutor(Protocol):
    spec: ToolSpec

    async def execute(self, call: ToolCall, *, grant: CapabilityGrant, cancel: asyncio.Event) -> ToolResult: ...


class ToolRegistry:
    """Holds executors. Empty in V1; ``execute`` enforces the grant rules."""

    def __init__(self) -> None:
        self._executors: dict[str, ToolExecutor] = {}
        self.audit: list[ToolAuditEntry] = []

    def register(self, executor: ToolExecutor) -> None:
        self._executors[executor.spec.name] = executor

    def specs(self) -> list[ToolSpec]:
        return [e.spec for e in self._executors.values()]

    async def execute(self, call: ToolCall, *, grant: CapabilityGrant, cancel: asyncio.Event) -> ToolResult:
        executor = self._executors.get(call.tool)
        if executor is None:
            return ToolResult(call_id=call.id, ok=False, error=f"unknown tool: {call.tool}")
        host = current_host_id()
        if not grant.is_valid_for(host, executor.spec.required_capabilities):
            result = ToolResult(call_id=call.id, ok=False, error="capability grant invalid for this host or expired")
        else:
            try:
                result = await asyncio.wait_for(
                    executor.execute(call, grant=grant, cancel=cancel), timeout=executor.spec.timeout_seconds
                )
            except asyncio.TimeoutError:
                result = ToolResult(call_id=call.id, ok=False, error="timeout")
        self.audit.append(ToolAuditEntry(call=call, result=result, host_id=host, at=datetime.now(timezone.utc)))
        return result
