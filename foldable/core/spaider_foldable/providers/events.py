"""Internal generation contract shared by every adapter.

A generation is an async stream of ``GenerationEvent``. The consumer (ChatService,
agent planner, connection test) only ever sees these kinds::

    start           the request was accepted by the provider (model, run ids)
    activity        provider-side progress that is real (prompt processing counts, queue)
    text_delta      visible answer text
    reasoning_delta reasoning text the provider marks as such (never persisted as facts)
    tool_call       a complete tool call (name + JSON arguments) requested by the model
    usage           token counts / timings (may arrive more than once; last wins)
    completed       the provider finished normally (finish_reason, usage)
    cancelled       the consumer stopped the stream
    error           the provider reported an error or the stream broke

Provider-specific continuation data (``provider_state``) rides along opaque; it is
never written into the memory store and never handed to another provider.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal, Optional

EventKind = Literal[
    "start",
    "activity",
    "text_delta",
    "reasoning_delta",
    "tool_call",
    "tool_result",
    "usage",
    "completed",
    "cancelled",
    "error",
]


@dataclass
class ToolCall:
    id: str
    name: str
    arguments: str  # raw JSON text as sent by the provider (parsed by the consumer)


@dataclass
class GenerationError(Exception):
    """Raised by adapters for failures before the stream starts; also carried in
    ``GenerationEvent.error`` for failures during the stream."""

    code: str  # unauthorized | forbidden | rate_limited | server_error | network | bad_request |
    #           not_found | unsupported | timeout | protocol | out_of_memory | empty_answer | cancelled
    message_de: str
    status: Optional[int] = None
    retryable: bool = False
    retry_after_s: Optional[float] = None
    raw: str = ""

    def __str__(self) -> str:  # pragma: no cover - trivial
        return f"{self.code}: {self.message_de}"

    def to_dict(self) -> dict:
        return {
            "code": self.code,
            "message_de": self.message_de,
            "status": self.status,
            "retryable": self.retryable,
            "retry_after_s": self.retry_after_s,
        }


@dataclass
class GenerationEvent:
    kind: EventKind
    text: str = ""
    reasoning: str = ""
    tool_call: Optional[ToolCall] = None
    usage: dict = field(default_factory=dict)
    activity: dict = field(default_factory=dict)  # e.g. {"phase": "prompt_processing", "total": n, "processed": n}
    finish_reason: Optional[str] = None
    error: Optional[GenerationError] = None
    provider_state: Optional[dict] = None  # opaque, adapter-local continuation data
    model: Optional[str] = None  # provider model id as reported by the provider


@dataclass
class GenerationRequest:
    """Canonical request. ``messages`` use the OpenAI-style roles system/user/assistant/tool;
    content is a string or a list of parts ({"type": "text", "text"} / {"type": "image_url",
    "image_url": {"url": data-url}}). Adapters translate to their wire format."""

    messages: list[dict]
    model: str
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = 1024
    stream: bool = True
    tools: Optional[list[dict]] = None  # OpenAI function-tool schema
    json_schema: Optional[dict] = None  # structured output request
    reasoning: Optional[str] = None  # "off" | "low" | "medium" | "high" | None (= provider default)
    stop: Optional[list[str]] = None
    extra: dict = field(default_factory=dict)  # adapter-specific, allowlisted by the preset
    timeout_s: float = 600.0
    run_id: str = ""


@dataclass
class ModelInfo:
    id: str
    display_name: str = ""
    owned_by: str = ""
    context_length: Optional[int] = None
    capabilities: dict = field(default_factory=dict)  # declared by the provider when available
    raw: dict = field(default_factory=dict)


@dataclass
class AccessCheck:
    """Result of a connection test; the four stages are reported separately."""

    server_reached: bool = False
    access_ok: Optional[bool] = None  # None = no reliable free check available
    model_listed: Optional[bool] = None
    answer_ok: Optional[bool] = None
    models: list[ModelInfo] = field(default_factory=list)
    message_de: str = ""
    error: Optional[dict] = None
    latency_ms: Optional[int] = None
    details: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        return {
            "server_reached": self.server_reached,
            "access_ok": self.access_ok,
            "model_listed": self.model_listed,
            "answer_ok": self.answer_ok,
            "models": [m.id for m in self.models][:200],
            "message_de": self.message_de,
            "error": self.error,
            "latency_ms": self.latency_ms,
            "details": self.details,
        }
