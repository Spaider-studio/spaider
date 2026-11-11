"""OpenAI Responses API adapter (``POST /responses``, SSE).

Used with an own API key (``openai`` preset) and by the optional ChatGPT
sign-in adapter (``openai_siwc``, which only differs in auth, the model list and
a stricter parameter allowlist: ``store=false`` + ``stream=true`` always, no
``max_output_tokens``/``temperature``, system text via ``instructions``).

Event names come from the installed SDK (openai 2.54.0, ``types/responses``):
``response.created``, ``response.in_progress``, ``response.output_item.added``,
``response.content_part.added``, ``response.output_text.delta/done``,
``response.refusal.delta/done``, ``response.function_call_arguments.delta/done``,
``response.reasoning_summary_text.delta``, ``response.reasoning_text.delta``,
``response.completed``, ``response.failed``, ``response.incomplete``, ``error``.
Unknown event types are ignored.
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

DEFAULT_ALLOWED = {"temperature", "max_output_tokens", "tools", "text", "reasoning", "instructions"}
SIWC_ALLOWED = {"tools", "text", "reasoning", "instructions"}


class ResponsesAdapter(ProviderAdapter):
    protocol = "responses"

    def __init__(self, cfg: AdapterConfig):
        super().__init__(cfg)
        self.allowed = set(cfg.param_allowlist) if cfg.param_allowlist is not None else set(DEFAULT_ALLOWED)
        self.force_store_false = bool(cfg.extra.get("store_false", False))
        self.models_path = cfg.extra.get("models_path", "models")

    async def list_models(self) -> list[ModelInfo]:
        try:
            r = await self._conn().get(self._url(self.models_path), headers=self._auth_headers(), timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            raise self.map_transport_error(exc) from exc
        if r.status_code != 200:
            raise self.map_http_error(r.status_code, r.text, r.headers)
        body = r.json()
        items = body.get("data") or body.get("models") or []
        out = []
        for m in items:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or m.get("slug") or "")
            if not mid:
                continue
            if m.get("visibility") not in (None, "list"):
                continue  # SIWC catalog: only display-ready models
            out.append(ModelInfo(id=mid, display_name=str(m.get("display_name") or mid), owned_by=str(m.get("owned_by") or "")))
        return out

    def _payload(self, req: GenerationRequest) -> dict:
        instructions_parts: list[str] = []
        inputs: list[dict] = []
        for m in req.messages:
            role = m.get("role")
            content = m.get("content")
            if role == "system":
                instructions_parts.append(content_to_text(content))
                continue
            if role == "tool":
                inputs.append({"type": "function_call_output", "call_id": str(m.get("tool_call_id") or ""), "output": content_to_text(content)})
                continue
            if isinstance(content, list):
                parts = []
                for p in content:
                    if p.get("type") == "text":
                        parts.append({"type": "input_text" if role == "user" else "output_text", "text": p.get("text") or ""})
                    elif p.get("type") == "image_url" and role == "user":
                        parts.append({"type": "input_image", "image_url": (p.get("image_url") or {}).get("url")})
                inputs.append({"role": role, "content": parts})
            else:
                inputs.append({"role": role, "content": content or ""})
        payload: dict = {"model": req.model, "input": inputs, "stream": True}
        if self.force_store_false or "store" in self.cfg.extra.get("request_defaults", {}):
            payload["store"] = False
        if instructions_parts and "instructions" in self.allowed:
            payload["instructions"] = "\n\n".join(instructions_parts)
        if "temperature" in self.allowed and req.temperature is not None:
            payload["temperature"] = req.temperature
        if "max_output_tokens" in self.allowed and req.max_tokens is not None:
            payload["max_output_tokens"] = req.max_tokens
        if req.tools and "tools" in self.allowed:
            payload["tools"] = [_to_responses_tool(t) for t in req.tools]
        if req.json_schema is not None and "text" in self.allowed:
            payload["text"] = {"format": {"type": "json_schema", "name": "decision", "schema": req.json_schema, "strict": False}}
        if req.reasoning and req.reasoning != "off" and "reasoning" in self.allowed:
            payload["reasoning"] = {"effort": req.reasoning}
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
        tool_frags: dict[str, dict] = {}
        resp: Optional[httpx.Response] = None
        terminal: Optional[str] = None
        try:
            try:
                resp = await self._conn().send(
                    self._conn().build_request("POST", self._url("responses"), headers=headers, json=payload, timeout=httpx.Timeout(req.timeout_s, connect=self.cfg.timeout_connect_s)),
                    stream=True,
                )
            except Exception as exc:  # noqa: BLE001
                yield GenerationEvent(kind="error", error=self.map_transport_error(exc))
                return
            self._active.add(resp)
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                yield GenerationEvent(kind="error", error=_siwc_aware(self.map_http_error(resp.status_code, body, resp.headers), body))
                return
            yield GenerationEvent(kind="start", model=req.model)
            async for _name, data in iter_sse(resp):
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if not isinstance(obj, dict):
                    continue
                et = str(obj.get("type") or _name or "")
                if et == "response.output_text.delta":
                    if first_ms is None:
                        first_ms = int((time.monotonic() - t0) * 1000)
                    got_any = True
                    yield GenerationEvent(kind="text_delta", text=str(obj.get("delta") or ""))
                elif et in ("response.reasoning_summary_text.delta", "response.reasoning_text.delta"):
                    yield GenerationEvent(kind="reasoning_delta", reasoning=str(obj.get("delta") or ""))
                elif et == "response.refusal.delta":
                    got_any = True
                    yield GenerationEvent(kind="text_delta", text=str(obj.get("delta") or ""))
                elif et == "response.output_item.added":
                    item = obj.get("item") or {}
                    if item.get("type") == "function_call":
                        tool_frags[str(item.get("id") or item.get("call_id") or len(tool_frags))] = {"call_id": item.get("call_id") or item.get("id") or "", "name": item.get("name") or "", "arguments": item.get("arguments") or ""}
                elif et == "response.function_call_arguments.delta":
                    key = str(obj.get("item_id") or "")
                    frag = tool_frags.setdefault(key, {"call_id": key, "name": "", "arguments": ""})
                    frag["arguments"] += str(obj.get("delta") or "")
                elif et == "response.function_call_arguments.done":
                    key = str(obj.get("item_id") or "")
                    frag = tool_frags.setdefault(key, {"call_id": key, "name": "", "arguments": ""})
                    if obj.get("arguments"):
                        frag["arguments"] = str(obj["arguments"])
                elif et == "response.output_item.done":
                    item = obj.get("item") or {}
                    if item.get("type") == "function_call":
                        key = str(item.get("id") or item.get("call_id") or "")
                        frag = tool_frags.setdefault(key, {"call_id": item.get("call_id") or key, "name": "", "arguments": ""})
                        frag["name"] = item.get("name") or frag["name"]
                        if item.get("arguments"):
                            frag["arguments"] = str(item["arguments"])
                        frag["call_id"] = item.get("call_id") or frag["call_id"]
                elif et == "response.completed":
                    terminal = "completed"
                    usage.update(_usage(obj))
                elif et == "response.incomplete":
                    terminal = "incomplete"
                    usage.update(_usage(obj))
                    reason = ((obj.get("response") or {}).get("incomplete_details") or {}).get("reason")
                    usage["incomplete_reason"] = reason
                elif et == "response.failed":
                    terminal = "failed"
                    err = ((obj.get("response") or {}).get("error") or {})
                    yield GenerationEvent(kind="error", error=_response_error(err))
                    return
                elif et == "error":
                    yield GenerationEvent(kind="error", error=_response_error(obj.get("error") or obj))
                    return
                elif et in ("response.queued", "response.in_progress"):
                    yield GenerationEvent(kind="activity", activity={"phase": "queued" if et.endswith("queued") else "in_progress"})
                # every other event type is tolerated
            for key, f in tool_frags.items():
                got_any = True
                yield GenerationEvent(kind="tool_call", tool_call=ToolCall(id=str(f["call_id"] or key), name=f["name"], arguments=f["arguments"] or "{}"))
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
        if terminal is None:
            yield GenerationEvent(kind="error", error=GenerationError("network", "Der Stream endete ohne Abschlussereignis (response.completed); die Antwort ist unvollständig.", retryable=True), usage=usage)
            return
        if terminal == "incomplete":
            yield GenerationEvent(kind="completed", finish_reason="length" if usage.get("incomplete_reason") == "max_output_tokens" else "incomplete", usage=usage)
            return
        if not got_any:
            yield GenerationEvent(kind="error", error=GenerationError("empty_answer", "Der Anbieter hat eine leere Antwort geliefert.", retryable=True), usage=usage)
            return
        yield GenerationEvent(kind="completed", finish_reason="stop", usage=usage)


def _usage(obj: dict) -> dict:
    u = ((obj.get("response") or {}).get("usage") or {})
    out = {}
    if u:
        out["prompt_tokens"] = int(u.get("input_tokens") or 0)
        out["completion_tokens"] = int(u.get("output_tokens") or 0)
        cached = ((u.get("input_tokens_details") or {}).get("cached_tokens"))
        if cached is not None:
            out["prompt_n_cached"] = int(cached)
    return out


def _to_responses_tool(tool: dict) -> dict:
    if tool.get("type") == "function" and isinstance(tool.get("function"), dict):
        fn = tool["function"]
        return {"type": "function", "name": fn.get("name"), "description": fn.get("description", ""), "parameters": fn.get("parameters", {"type": "object"})}
    return tool


def _response_error(err) -> GenerationError:
    code = str((err or {}).get("code") or "") if isinstance(err, dict) else ""
    msg = str((err or {}).get("message") or err)[:300] if isinstance(err, dict) else str(err)[:300]
    if code == "subscription_sharing_usage_limit_exceeded":
        return GenerationError("rate_limited", "Das Nutzungslimit des ChatGPT-Plans ist erreicht; bitte warten (ChatGPT → Einstellungen → Nutzung).", 429, retryable=False, raw=code)
    if code == "subscription_sharing_usage_unavailable":
        return GenerationError("server_error", "Die Nutzungsprüfung des ChatGPT-Plans ist vorübergehend nicht verfügbar; später erneut versuchen.", 503, retryable=True, raw=code)
    if code == "subscription_sharing_user_not_eligible":
        return GenerationError("forbidden", "Der ChatGPT-Workspace erlaubt diese Nutzung nicht (Richtlinie); nicht wiederholen.", 403, raw=code)
    return GenerationError("server_error", f"Der Anbieter meldet einen Fehler: {msg}", raw=code or msg)


def _siwc_aware(err: GenerationError, body: str) -> GenerationError:
    for code in ("subscription_sharing_usage_limit_exceeded", "subscription_sharing_usage_unavailable", "subscription_sharing_user_not_eligible"):
        if code in body:
            return _response_error({"code": code, "message": body[:200]})
    return err
