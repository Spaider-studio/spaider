"""OpenAI-compatible ``/chat/completions`` adapter.

Used for the bundled llama.cpp server (with its extensions), DeepSeek, Z.ai,
Mistral, OpenRouter, Ollama, LM Studio, the Hugging Face router, the Gemini
compatibility layer and any own server speaking this protocol. Differences
between them are expressed through ``AdapterConfig.param_allowlist`` and
``extra`` (set by the preset), never through hard-coded brand checks.

Stream handling: ``data:`` chunks with ``choices[].delta`` (``content``,
``reasoning_content``, ``tool_calls`` fragments), ``usage``/``timings`` blocks,
``prompt_progress`` (llama.cpp), ``[DONE]``; an ``{"error": …}`` object in the
stream (llama.cpp sends one when decode fails) becomes an ``error`` event; an
HTTP error status before the stream becomes a ``GenerationError``.
"""

from __future__ import annotations

import json
import logging
import time
from typing import AsyncIterator, Optional

import httpx

from .base import AdapterConfig, ProviderAdapter, content_to_text, has_images
from .events import GenerationError, GenerationEvent, GenerationRequest, ModelInfo, ToolCall
from .sse import is_done_sentinel, iter_sse

logger = logging.getLogger(__name__)

DEFAULT_ALLOWED = {"temperature", "max_tokens", "stream", "tools", "tool_choice", "response_format", "stop", "stream_options"}


class ChatCompletionsAdapter(ProviderAdapter):
    protocol = "chat_completions"

    def __init__(self, cfg: AdapterConfig):
        super().__init__(cfg)
        self.allowed = set(cfg.param_allowlist) if cfg.param_allowlist is not None else set(DEFAULT_ALLOWED)
        self.supports_images = bool(cfg.extra.get("vision", True))
        self.reasoning_passthrough = bool(cfg.extra.get("reasoning_passthrough", False))  # DeepSeek with tools
        self.model_list_path = cfg.extra.get("models_path", "models")
        self.completions_path = cfg.extra.get("completions_path", "chat/completions")
        self.usage_in_stream = bool(cfg.extra.get("stream_usage", True))

    # ---- models ----------------------------------------------------------------
    async def list_models(self) -> list[ModelInfo]:
        try:
            r = await self._conn().get(self._url(self.model_list_path), headers=self._auth_headers(), timeout=20.0)
        except Exception as exc:  # noqa: BLE001
            raise self.map_transport_error(exc) from exc
        if r.status_code != 200:
            raise self.map_http_error(r.status_code, r.text, r.headers)
        try:
            body = r.json()
        except ValueError as exc:
            raise GenerationError("protocol", "Modellliste ist kein JSON.") from exc
        items = body.get("data") if isinstance(body, dict) else body
        out: list[ModelInfo] = []
        for m in items or []:
            if not isinstance(m, dict):
                continue
            mid = str(m.get("id") or m.get("name") or "")
            if not mid:
                continue
            caps = {}
            if isinstance(m.get("architecture"), dict):
                caps["input_modalities"] = m["architecture"].get("input_modalities")
            for k in ("context_length", "supports_tools", "supports_structured_output"):
                if k in m:
                    caps[k] = m[k]
            out.append(ModelInfo(id=mid, display_name=str(m.get("display_name") or m.get("name") or mid), owned_by=str(m.get("owned_by") or ""), context_length=m.get("context_length"), capabilities=caps, raw={k: v for k, v in m.items() if k in ("id", "owned_by", "created", "context_length", "pricing", "providers", "capabilities")}))
        return out

    # ---- generation ------------------------------------------------------------
    def _payload(self, req: GenerationRequest) -> dict:
        messages = req.messages if self.supports_images else [{**m, "content": content_to_text(m.get("content"))} for m in req.messages]
        payload: dict = {"model": req.model, "messages": messages, "stream": req.stream}
        if "temperature" in self.allowed and req.temperature is not None:
            payload["temperature"] = req.temperature
        if "max_tokens" in self.allowed and req.max_tokens is not None:
            payload["max_tokens"] = req.max_tokens
        if req.stream and "stream_options" in self.allowed and self.usage_in_stream:
            payload["stream_options"] = {"include_usage": True}
        if req.tools and "tools" in self.allowed:
            payload["tools"] = req.tools
        if req.json_schema is not None and "response_format" in self.allowed:
            payload["response_format"] = {"type": "json_schema", "json_schema": {"name": "decision", "schema": req.json_schema}}
        if req.stop and "stop" in self.allowed:
            payload["stop"] = req.stop
        # reasoning control is provider-specific: the preset maps it to the right field
        rmap = self.cfg.extra.get("reasoning_param")  # e.g. {"field": "reasoning_effort"} or {"field": "thinking", "off": {...}, "on": {...}}
        if req.reasoning is not None and rmap:
            field = rmap.get("field")
            if field == "reasoning_effort" and req.reasoning != "off":
                payload["reasoning_effort"] = req.reasoning
            elif field == "thinking":
                payload["thinking"] = rmap.get("off") if req.reasoning == "off" else rmap.get("on")
            elif field == "chat_template_kwargs":
                payload["chat_template_kwargs"] = {rmap.get("toggle", "enable_thinking"): req.reasoning != "off"}
        for k, v in (req.extra or {}).items():
            if k in self.allowed or k in self.cfg.extra.get("extra_allowed", ()):  # preset-declared extras only
                payload[k] = v
        for k, v in (self.cfg.extra.get("request_defaults") or {}).items():
            payload.setdefault(k, v)
        return payload

    async def generate(self, req: GenerationRequest) -> AsyncIterator[GenerationEvent]:
        if has_images(req.messages) and not self.supports_images:
            yield GenerationEvent(kind="error", error=GenerationError("unsupported", "Dieses Modell/diese Verbindung unterstützt keine Bildeingaben."))
            return
        payload = self._payload(req)
        headers = {**self._auth_headers(), "Content-Type": "application/json", "Accept": "text/event-stream" if req.stream else "application/json"}
        t0 = time.monotonic()
        first_ms: Optional[int] = None
        usage: dict = {}
        finish: Optional[str] = None
        tool_frags: dict[int, dict] = {}
        got_any = False
        resp: Optional[httpx.Response] = None
        try:
            try:
                resp = await self._conn().send(
                    self._conn().build_request("POST", self._url(self.completions_path), headers=headers, json=payload, timeout=httpx.Timeout(req.timeout_s, connect=self.cfg.timeout_connect_s)),
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
            ctype = (resp.headers.get("content-type") or "").lower()
            if not req.stream or ("json" in ctype and "event-stream" not in ctype and "ndjson" not in ctype):
                # non-streaming answer (or a server that ignored stream=true)
                body = (await resp.aread()).decode("utf-8", errors="replace")
                try:
                    obj = json.loads(body)
                except ValueError:
                    yield GenerationEvent(kind="error", error=GenerationError("protocol", "Antwort war weder Stream noch JSON.", raw=body[:200]))
                    return
                if isinstance(obj, dict) and obj.get("error"):
                    yield GenerationEvent(kind="error", error=_stream_error(obj["error"]))
                    return
                for ev in _events_from_full(obj):
                    got_any = got_any or ev.kind in ("text_delta", "tool_call")
                    yield ev
                usage = (obj.get("usage") or {}) if isinstance(obj, dict) else {}
                finish = ((obj.get("choices") or [{}])[0].get("finish_reason")) if isinstance(obj, dict) else None
            else:
                async for _event, data in iter_sse(resp):
                    if is_done_sentinel(data):
                        break
                    try:
                        obj = json.loads(data)
                    except ValueError:
                        continue  # unknown non-JSON line
                    if not isinstance(obj, dict):
                        continue
                    if obj.get("error"):
                        yield GenerationEvent(kind="error", error=_stream_error(obj["error"]))
                        return
                    if obj.get("prompt_progress"):
                        p = obj["prompt_progress"]
                        yield GenerationEvent(kind="activity", activity={"phase": "prompt_processing", "total": p.get("total"), "processed": p.get("processed"), "cache": p.get("cache")})
                    if obj.get("usage"):
                        usage.update(obj["usage"])
                    if obj.get("timings"):
                        usage["timings"] = obj["timings"]
                    for choice in obj.get("choices") or []:
                        delta = choice.get("delta") or choice.get("message") or {}
                        text = delta.get("content") or ""
                        reasoning = delta.get("reasoning_content") or delta.get("reasoning") or ""
                        if isinstance(text, list):  # some servers send parts
                            text = "".join(str(p.get("text") or "") for p in text if isinstance(p, dict))
                        if (text or reasoning) and first_ms is None:
                            first_ms = int((time.monotonic() - t0) * 1000)
                        if reasoning:
                            yield GenerationEvent(kind="reasoning_delta", reasoning=reasoning)
                        if text:
                            got_any = True
                            yield GenerationEvent(kind="text_delta", text=text)
                        for tc in delta.get("tool_calls") or []:
                            idx = int(tc.get("index", 0))
                            frag = tool_frags.setdefault(idx, {"id": tc.get("id") or "", "name": "", "arguments": ""})
                            if tc.get("id"):
                                frag["id"] = tc["id"]
                            fn = tc.get("function") or {}
                            if fn.get("name"):
                                frag["name"] += fn["name"] if not frag["name"] else ""
                            if fn.get("arguments"):
                                frag["arguments"] += fn["arguments"]
                        if choice.get("finish_reason"):
                            finish = choice["finish_reason"]
            for idx in sorted(tool_frags):
                f = tool_frags[idx]
                got_any = True
                yield GenerationEvent(kind="tool_call", tool_call=ToolCall(id=f["id"] or f"call_{idx}", name=f["name"], arguments=f["arguments"] or "{}"))
        except httpx.HTTPError as exc:
            yield GenerationEvent(kind="error", error=self.map_transport_error(exc))
            return
        except GenerationError as exc:
            yield GenerationEvent(kind="error", error=exc)
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
        timings = usage.get("timings") or {}
        if timings.get("predicted_per_second"):
            usage["tokens_per_second"] = round(float(timings["predicted_per_second"]), 2)
        if timings.get("prompt_per_second"):
            usage["prompt_tokens_per_second"] = round(float(timings["prompt_per_second"]), 2)
        if timings.get("prompt_n") is not None:
            usage["prompt_n_processed"] = timings.get("prompt_n")
        if timings.get("cache_n") is not None:
            usage["prompt_n_cached"] = timings.get("cache_n")
        if usage:
            yield GenerationEvent(kind="usage", usage=dict(usage))
        if not got_any and finish not in ("tool_calls", "function_call"):
            # finished without any visible text, tool call or error: never "done" silently
            yield GenerationEvent(kind="error", error=GenerationError("empty_answer", "Der Anbieter hat eine leere Antwort geliefert (kein Text, kein Werkzeugaufruf, kein Fehler).", retryable=True), finish_reason=finish, usage=usage)
            return
        yield GenerationEvent(kind="completed", finish_reason=finish, usage=usage)


def _stream_error(err) -> GenerationError:
    msg = ""
    code = "server_error"
    if isinstance(err, dict):
        msg = str(err.get("message") or err.get("type") or err)[:300]
        etype = str(err.get("type") or err.get("code") or "").lower()
        if "memory" in msg.lower() or "outofdevicememory" in msg.lower().replace(" ", ""):
            code = "out_of_memory"
        elif "context" in msg.lower() and ("exceed" in msg.lower() or "size" in msg.lower()):
            code = "bad_request"
        elif "rate" in etype or "429" in etype:
            code = "rate_limited"
    else:
        msg = str(err)[:300]
    de = {
        "out_of_memory": "Dem Modell ging während der Verarbeitung der Grafik- oder Arbeitsspeicher aus.",
        "bad_request": "Die Anfrage überschreitet die Grenzen des Modells (z. B. Kontextlänge).",
        "rate_limited": "Der Anbieter hat ein Limit gemeldet.",
    }.get(code, "Der Modellserver hat einen Fehler gemeldet.")
    return GenerationError(code, f"{de} ({msg})", retryable=code in ("rate_limited", "out_of_memory"), raw=msg)


def _events_from_full(obj: dict):
    for choice in obj.get("choices") or []:
        msg = choice.get("message") or {}
        if msg.get("reasoning_content"):
            yield GenerationEvent(kind="reasoning_delta", reasoning=str(msg["reasoning_content"]))
        content = msg.get("content")
        text = content_to_text(content) if isinstance(content, list) else (content or "")
        if text:
            yield GenerationEvent(kind="text_delta", text=text)
        for tc in msg.get("tool_calls") or []:
            fn = tc.get("function") or {}
            yield GenerationEvent(kind="tool_call", tool_call=ToolCall(id=tc.get("id") or "call_0", name=fn.get("name") or "", arguments=fn.get("arguments") or "{}"))
