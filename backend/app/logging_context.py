"""Request-scoped context variables for structured logging.

`request_id_var` and `agent_id_var` are set at request entry (HTTP middleware or
Kafka consumer per-message) and read by the logging `ContextFilter` to stamp
every log line emitted within that request's task.

Contextvars propagate across `await` boundaries within the same asyncio task,
which is why service-layer code (`SemanticCompressor.extract`,
`GraphService.write_graph`) does not need to thread these through its call
signatures to keep log correlation.
"""
from __future__ import annotations

from contextvars import ContextVar, Token
from typing import Optional

request_id_var: ContextVar[Optional[str]] = ContextVar("spaider_request_id", default=None)
agent_id_var: ContextVar[Optional[str]] = ContextVar("spaider_agent_id", default=None)


def bind_request_context(
    *,
    request_id: Optional[str] = None,
    agent_id: Optional[str] = None,
) -> tuple[Optional[Token], Optional[Token]]:
    """Set contextvars and return reset tokens. Pass `None` to leave a var unchanged."""
    rid_token = request_id_var.set(request_id) if request_id is not None else None
    aid_token = agent_id_var.set(agent_id) if agent_id is not None else None
    return rid_token, aid_token


def reset_request_context(tokens: tuple[Optional[Token], Optional[Token]]) -> None:
    rid_token, aid_token = tokens
    if rid_token is not None:
        request_id_var.reset(rid_token)
    if aid_token is not None:
        agent_id_var.reset(aid_token)


def get_request_id() -> Optional[str]:
    return request_id_var.get()


def get_agent_id() -> Optional[str]:
    return agent_id_var.get()
