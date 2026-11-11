"""Builds adapters for connections (secret resolution, preset extras) and
wraps the embedded llama.cpp runtime in the same GenerationEvent contract."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import AsyncIterator, Callable, Optional

from .anthropic import AnthropicMessagesAdapter
from .base import AdapterConfig, ProviderAdapter, is_private_url
from .chat_completions import ChatCompletionsAdapter
from .connections import CapabilityProfile, ConnectionStore, ProviderConnection
from .credentials import CredentialError, CredentialStore
from .events import AccessCheck, GenerationError, GenerationEvent, GenerationRequest, ModelInfo
from .presets import ProviderPreset
from .responses import ResponsesAdapter

logger = logging.getLogger(__name__)

ADAPTERS = {
    "chat_completions": ChatCompletionsAdapter,
    "responses": ResponsesAdapter,
    "anthropic_messages": AnthropicMessagesAdapter,
}


class LocalRuntimeAdapter(ProviderAdapter):
    """The bundled llama-server through the existing ``LocalLLMClient`` (progress,
    prompt cache, reasoning toggle, grammar JSON stay as they are)."""

    protocol = "llama_local"

    def __init__(self, get_client: Callable, reasoning_toggle: Callable[[], Optional[str]]):
        # no HTTP setup of our own: the client is owned by the InferenceManager
        self.get_client = get_client
        self.reasoning_toggle = reasoning_toggle
        self._active = set()
        self.cfg = AdapterConfig(connection_id="local", provider="llama_local", base_url="http://127.0.0.1:1", execution="local", allow_insecure_http=True)
        self.origin = "local"

    async def list_models(self) -> list[ModelInfo]:
        return []

    async def cancel(self) -> None:  # cancellation is the stop_event of stream_chat
        return None

    async def generate(self, req: GenerationRequest, *, stop_event: Optional[asyncio.Event] = None) -> AsyncIterator[GenerationEvent]:
        client = self.get_client()
        if client is None:
            yield GenerationEvent(kind="error", error=GenerationError("runtime_not_ready", "Die lokale Modell-Laufzeit ist nicht bereit."))
            return
        toggle = self.reasoning_toggle()
        enable = None
        if toggle:
            enable = (req.reasoning not in (None, "off")) if req.reasoning is not None else False
        yield GenerationEvent(kind="start", model=req.model)
        got_any = False
        usage: dict = {}
        try:
            async for chunk in client.stream_chat(
                req.messages,
                temperature=req.temperature if req.temperature is not None else 0.7,
                max_tokens=req.max_tokens or 1024,
                enable_thinking=enable,
                reasoning_toggle=toggle,
                json_schema=req.json_schema,
                tools=req.tools,
                stop_event=stop_event,
                timeout_s=req.timeout_s,
            ):
                if chunk.progress:
                    p = chunk.progress
                    yield GenerationEvent(kind="activity", activity={"phase": "prompt_processing", "total": p.get("total"), "processed": p.get("processed"), "cache": p.get("cache")})
                    continue
                if chunk.done:
                    usage = chunk.usage or {}
                    break
                if chunk.reasoning:
                    yield GenerationEvent(kind="reasoning_delta", reasoning=chunk.reasoning)
                if chunk.text:
                    got_any = True
                    yield GenerationEvent(kind="text_delta", text=chunk.text)
        except RuntimeError as exc:  # llama-server HTTP/stream error raised by the client
            yield GenerationEvent(kind="error", error=_local_error(str(exc)))
            return
        except Exception as exc:  # noqa: BLE001
            yield GenerationEvent(kind="error", error=self.map_transport_error(exc))
            return
        if stop_event is not None and stop_event.is_set():
            yield GenerationEvent(kind="cancelled", usage=usage)
            return
        if usage:
            yield GenerationEvent(kind="usage", usage=dict(usage))
        tool_calls = usage.get("tool_calls") or []
        if not got_any and not tool_calls:
            yield GenerationEvent(kind="error", error=GenerationError("empty_answer", "Das Modell hat keine Antwort erzeugt (kein Text, kein Werkzeugaufruf, kein Fehler).", retryable=True), usage=usage)
            return
        yield GenerationEvent(kind="completed", finish_reason=usage.get("finish_reason"), usage=usage)


def _local_error(text: str) -> GenerationError:
    low = text.lower()
    if "outofdevicememory" in low.replace(" ", "") or "out of memory" in low or "allocatememory" in low:
        return GenerationError("out_of_memory", "Dem Modell ist der Grafik- oder Arbeitsspeicher ausgegangen. Empfehlung: Modell mit weniger GPU-Schichten oder auf CPU neu laden, Kontext verkleinern oder ein kleineres Modell wählen.", retryable=True, raw=text[:300])
    if "context" in low and ("exceed" in low or "n_ctx" in low or "size" in low):
        return GenerationError("bad_request", "Die Anfrage überschreitet die Kontextlänge des Modells.", raw=text[:300])
    if "http 5" in low or "http 4" in low:
        return GenerationError("server_error", f"Der lokale Modellserver hat einen Fehler gemeldet: {text[:200]}", retryable=True, raw=text[:300])
    return GenerationError("server_error", f"Fehler der lokalen Laufzeit: {text[:200]}", raw=text[:300])


class ProviderRegistry:
    """Adapters per connection id (cached per process), presets and connection tests."""

    def __init__(self, presets: dict[str, ProviderPreset], connections: ConnectionStore, credentials: CredentialStore, *, local_adapter: Optional[LocalRuntimeAdapter] = None):
        self.presets = presets
        self.connections = connections
        self.credentials = credentials
        self.local_adapter = local_adapter
        self._adapters: dict[str, ProviderAdapter] = {}
        self._siwc_token_provider: Optional[Callable[[ProviderConnection], "asyncio.Future[str]"]] = None

    def set_siwc_token_provider(self, fn) -> None:
        self._siwc_token_provider = fn

    def preset(self, pid: str) -> ProviderPreset:
        p = self.presets.get(pid)
        if p is None:
            raise GenerationError("not_found", f"Unbekanntes Anbieter-Preset: {pid}")
        return p

    async def adapter_for(self, conn: ProviderConnection) -> ProviderAdapter:
        fingerprint = (conn.updated_at, conn.credential_ref, conn.base_url, conn.auth_method, conn.execution, json.dumps(conn.extra, sort_keys=True))
        cached = self._adapters.get(conn.id)
        if cached is not None and getattr(cached, "_conn_fingerprint", None) == fingerprint:
            return cached
        if cached is not None:
            await cached.aclose()
        preset = self.preset(conn.provider)
        api_key: Optional[str] = None
        if conn.auth_method == "api_key":
            try:
                api_key = self.credentials.get(conn.credential_ref)
            except CredentialError as exc:
                raise GenerationError("credential", exc.detail) from exc
            if not api_key:
                raise GenerationError("credential", "Für diese Verbindung ist kein Schlüssel hinterlegt (oder der Tresor ist gesperrt).")
        elif conn.auth_method == "oauth_siwc":
            if self._siwc_token_provider is None:
                raise GenerationError("unsupported", "ChatGPT-Anmeldung ist nicht eingerichtet.")
            api_key = await self._siwc_token_provider(conn)
        base_url = conn.base_url or preset.base_url
        if not base_url:
            raise GenerationError("bad_request", "Für diese Verbindung fehlt die Serveradresse.")
        extra = {**preset.extra, **(conn.extra.get("adapter") or {}), "models_path": preset.models_path, "completions_path": preset.completions_path}
        headers = dict(preset.extra.get("static_headers") or {})
        headers.update(conn.extra.get("headers") or {})
        cfg = AdapterConfig(
            connection_id=conn.id,
            provider=conn.provider,
            base_url=base_url,
            api_key=api_key,
            headers=headers,
            execution=conn.execution,
            allow_insecure_http=bool(conn.extra.get("allow_insecure_http")) or is_private_url(base_url) and conn.execution in ("local", "lan"),
            param_allowlist=set(preset.param_allowlist) if preset.param_allowlist is not None else None,
            extra=extra,
        )
        cls = ADAPTERS.get(preset.protocol)
        if cls is None:
            raise GenerationError("unsupported", f"Protokoll {preset.protocol} hat keinen Adapter.")
        adapter = cls(cfg)
        adapter._conn_fingerprint = fingerprint  # type: ignore[attr-defined]
        self._adapters[conn.id] = adapter
        return adapter

    async def drop(self, conn_id: str) -> None:
        a = self._adapters.pop(conn_id, None)
        if a is not None:
            await a.aclose()

    async def close_all(self) -> None:
        for cid in list(self._adapters):
            await self.drop(cid)

    async def test_connection(self, conn: ProviderConnection, *, probe_model: Optional[str] = None, allow_generation: bool = False) -> AccessCheck:
        """Four separate stages; generation only when explicitly allowed (may cost money)."""
        preset = self.preset(conn.provider)
        try:
            adapter = await self.adapter_for(conn)
        except GenerationError as exc:
            res = AccessCheck(message_de=exc.message_de, error=exc.to_dict())
            return res
        res = await adapter.check_access(probe_model=probe_model or conn.default_model, allow_generation=allow_generation)
        if not preset.free_access_check and res.access_ok:
            res.details["note_de"] = "Der Anbieter bietet keine kostenlose Zugangsprüfung; die Modellliste belegt nur die Erreichbarkeit."
        if res.models:
            scope = f"connection:{conn.id}"
            for m in res.models[:200]:
                self.connections.save_capability(
                    CapabilityProfile(
                        scope=scope,
                        model=m.id,
                        text=preset.capabilities.text,
                        vision=_declared(m.capabilities.get("input_modalities"), "image", preset.capabilities.vision),
                        tools=_flag(m.capabilities.get("supports_tools"), preset.capabilities.tools),
                        structured_output=_flag(m.capabilities.get("supports_structured_output"), preset.capabilities.structured_output),
                        streaming=preset.capabilities.streaming,
                        reasoning=preset.capabilities.reasoning,
                        context_length=m.context_length,
                        notes_de=preset.capabilities.notes_de,
                        checked_at=time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
                    )
                )
        if res.answer_ok and (probe_model or conn.default_model):
            scope = f"connection:{conn.id}"
            prof = self.connections.capability(scope, probe_model or conn.default_model) or CapabilityProfile(scope=scope, model=probe_model or conn.default_model)
            prof.text = "verified"
            prof.streaming = "verified"
            prof.checked_at = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
            self.connections.save_capability(prof)
        self.connections.update(conn.id, last_test={**res.to_dict(), "at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())})
        return res


def _declared(modalities, want: str, default: str) -> str:
    if isinstance(modalities, list):
        return "declared" if want in modalities else "no"
    return default


def _flag(value, default: str) -> str:
    if value is True:
        return "declared"
    if value is False:
        return "no"
    return default
