"""Anthropic Messages API adapter (``POST /v1/messages``, SSE content blocks).

Wire events (documentation 2026-11-11): ``message_start``, ``content_block_start``
(``text`` | ``tool_use`` | ``thinking``), ``content_block_delta`` (``text_delta``,
``input_json_delta``/``partial_json``, ``thinking_delta``, ``signature_delta``),
``content_block_stop``, ``message_delta`` (``stop_reason``, ``usage``),
``message_stop``, ``ping``, ``error``. Unknown events are ignored.
"""

from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

from .base import AdapterConfig, ProviderAdapter, content_to_text
from .events import GenerationError, GenerationEvent, GenerationRequest, ModelInfo, ToolCall
from .sse import iter_sse

logger = logging.getLogger(__name__)

DEFAULT_VERSION = "2026-10-21"
DEFAULT_ALLOWED = {"temperature", "max_tokens", "tools", "stop_sequences", "thinking", "system"}


class AnthropicMessagesAdapter(ProviderAdapter):
    protocol = "anthropic_messages"

    def __init__(self, cfg: AdapterConfig):
        super().__init__(cfg)
        self.version = str(cfg.extra.get("anthropic_version") or DEFAULT_VERSION)
        self.allowed = set(cfg.param_allowlist) if cfg.param_allowlist is not None else set(DEFAULT_ALLOWED)

    def _auth_headers(self) -> dict:
        h = {"anthropic-version": self.version}
        if self.cfg.api_key:
            h["x-api-key"] = self.cfg.api_key
        return h

    async def list_models(self) -> list[ModelInfo]:
        try:
            r = await self._conn().get(self._url("models"), headers=self._auth_headers(), params={"limit": 100}, timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            raise self.map_transport_error(exc) from exc
        if r.status_code != 200:
            raise self.map_http_error(r.status_code, r.text, r.headers)
        body = r.json()
        out = []
        for m in body.get("data") or []:
            if isinstance(m, dict) and m.get("id"):
                out.append(ModelInfo(id=str(m["id"]), display_name=str(m.get("display_name") or m["id"]), owned_by="anthropic"))
        return out

    def _payload(self, req: GenerationRequest) -> dict:
        system_parts: list[str] = []
        msgs: list[dict] = []
        for m in req.messages:
            role = m.get("role")
            content = m.get("content")
            if role == "system":
                system_parts.append(content_to_text(content))
                continue
            if role == "tool":
                msgs.append({"role": "user", "content": [{"type": "tool_result", "tool_use_id": str(m.get("tool_call_id") or ""), "content": content_to_text(content)}]})
                continue
            if isinstance(content, list):
                blocks = []
                for p in content:
                    if p.get("type") == "text":
                        blocks.append({"type": "text", "text": p.get("text") or ""})
                    elif p.get("type") == "image_url":
                        url = (p.get("image_url") or {}).get("url") or ""
                        if url.startswith("data:"):
                            header, _, b64 = url.partition(",")
                            media = header[5:].split(";")[0] or "image/png"
                            blocks.append({"type": "image", "source": {"type": "base64", "media_type": media, "data": b64}})
                        else:
                            blocks.append({"type": "image", "source": {"type": "url", "url": url}})
                msgs.append({"role": "assistant" if role == "assistant" else "user", "content": blocks})
            else:
                msgs.append({"role": "assistant" if role == "assistant" else "user", "content": content or ""})
        # Anthropic requires alternating roles starting with user; merge consecutive same-role turns
        merged: list[dict] = []
        for m in msgs:
            if merged and merged[-1]["role"] == m["role"]:
                a, b = merged[-1]["content"], m["content"]
                merged[-1]["content"] = _as_blocks(a) + _as_blocks(b)
            else:
                merged.append(m)
        if merged and merged[0]["role"] != "user":
            merged.insert(0, {"role": "user", "content": "(Fortsetzung)"})
        payload: dict = {"model": req.model, "messages": merged, "max_tokens": int(req.max_tokens or 1024), "stream": True}
        if system_parts and "system" in self.allowed:
            payload["system"] = "\n\n".join(system_parts)
        if "temperature" in self.allowed and req.temperature is not None:
            payload["temperature"] = req.temperature
        if req.tools and "tools" in self.allowed:
            payload["tools"] = [_to_anthropic_tool(t) for t in req.tools]
        if req.stop and "stop_sequences" in self.allowed:
            payload["stop_sequences"] = req.stop
        if req.reasoning and req.reasoning != "off" and "thinking" in self.allowed:
            budget = {"low": 1024, "medium": 4096, "high": 16000}.get(req.reasoning, 4096)
            payload["thinking"] = {"type": "enabled", "budget_tokens": budget}
            payload["max_tokens"] = max(payload["max_tokens"], budget + 512)
            payload.pop("temperature", None)  # not allowed together with thinking
        for k, v in (self.cfg.extra.get("request_defaults") or {}).items():
            payload.setdefault(k, v)
        return payload

    async def generate(self, req: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        payload = self._payload(req)
        headers = {**self._auth_headers(), "Content-Type": "application/json", "Accept": "text/event-stream"}
        t0 = time.monotonic()
        first_ms: Optional[int] = None
        usage: dict = {}
        got_any = False
        stop_reason: Optional[str] = None
        blocks: dict[int, dict] = {}
        resp: Optional[httpx.Response] = None
        saw_stop = False
        try:
            try:
                resp = await self._conn().send(
                    self._conn().build_request("POST", self._url("messages"), headers=headers, json=payload, timeout=httpx.Timeout(req.timeout_s, connect=self.cfg.timeout_connect_s)),
                    stream=True,
                )
            except Exception as exc:  # noqa: BLE001
                yield GenerationEvent(kind="error", error=self.map_transport_error(exc))
                return
            self._active.add(resp)
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                yield GenerationEvent(kind="error", error=self.map_http_error(resp.status_code, body, resp.headers))
                return
            yield GenerationEvent(kind="start", model=req.model)
            async for name, data in iter_sse(resp):
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                et = str(obj.get("type") or name or "")
                if et == "message_start":
                    u = (obj.get("message") or {}).get("usage") or {}
                    if u:
                        usage["prompt_tokens"] = int(u.get("input_tokens") or 0)
                        if u.get("cache_read_input_tokens") is not None:
                            usage["prompt_n_cached"] = int(u.get("cache_read_input_tokens") or 0)
                elif et == "content_block_start":
                    idx = int(obj.get("index", 0))
                    cb = obj.get("content_block") or {}
                    blocks[idx] = {"type": cb.get("type"), "id": cb.get("id"), "name": cb.get("name"), "json": ""}
                elif et == "content_block_delta":
                    idx = int(obj.get("index", 0))
                    d = obj.get("delta") or {}
                    dt = d.get("type")
                    if dt == "text_delta":
                        if first_ms is None:
                            first_ms = int((time.monotonic() - t0) * 1000)
                        got_any = True
                        yield GenerationEvent(kind="text_delta", text=str(d.get("text") or ""))
                    elif dt == "thinking_delta":
                        yield GenerationEvent(kind="reasoning_delta", reasoning=str(d.get("thinking") or ""))
                    elif dt == "input_json_delta":
                        blocks.setdefault(idx, {"type": "tool_use", "id": None, "name": None, "json": ""})["json"] += str(d.get("partial_json") or "")
                elif et == "content_block_stop":
                    idx = int(obj.get("index", 0))
                    b = blocks.get(idx)
                    if b and b.get("type") == "tool_use":
                        got_any = True
                        yield GenerationEvent(kind="tool_call", tool_call=ToolCall(id=str(b.get("id") or f"toolu_{idx}"), name=str(b.get("name") or ""), arguments=b.get("json") or "{}"))
                elif et == "message_delta":
                    stop_reason = (obj.get("delta") or {}).get("stop_reason") or stop_reason
                    u = obj.get("usage") or {}
                    if u.get("output_tokens") is not None:
                        usage["completion_tokens"] = int(u["output_tokens"])
                elif et == "message_stop":
                    saw_stop = True
                elif et == "error":
                    err = obj.get("error") or {}
                    etype = str(err.get("type") or "")
                    code = "rate_limited" if etype in ("overloaded_error", "rate_limit_error") else "server_error"
                    yield GenerationEvent(kind="error", error=GenerationError(code, f"Anthropic meldet: {err.get('message') or etype}", retryable=code == "rate_limited", raw=etype))
                    return
                # ping and unknown events are ignored
        except httpx.HTTPError as exc:
            yield GenerationEvent(kind="error", error=self.map_transport_error(exc))
            return
        finally:
            if resp is not None:
                self._active.discard(resp)
                try:
                    await resp.aclose()
                except Exception:  # noqa: BLE001
                    pass
        if first_ms is not None:
            usage["time_to_first_token_ms"] = first_ms
        if usage:
            yield GenerationEvent(kind="usage", usage=dict(usage))
        if not saw_stop:
            yield GenerationEvent(kind="error", error=GenerationError("network", "Der Stream endete ohne message_stop; die Antwort ist unvollständig.", retryable=True), usage=usage)
            return
        if not got_any:
            yield GenerationEvent(kind="error", error=GenerationError("empty_answer", "Der Anbieter hat eine leere Antwort geliefert.", retryable=True), usage=usage)
            return
        finish = {"end_turn": "stop", "max_tokens": "length", "tool_use": "tool_calls", "stop_sequence": "stop"}.get(stop_reason or "", stop_reason)
        yield GenerationEvent(kind="completed", finish_reason=finish, usage=usage)


def _as_blocks(content) -> list:
    if isinstance(content, str):
        return [{"type": "text", "text": content}]
    return list(content)


def _to_anthropic_tool(tool: dict) -> dict:
    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        fn = tool["function"]
        return {"name": fn.get("name"), "description": fn.get("description", ""), "input_schema": fn.get("parameters", {"type": "object"})}
    return tool
