"""Provider presets: the first concrete connection matrix over the three wire
protocols. A preset declares protocol, default base URL, allowed auth methods,
model-list path, parameter allowlist, capability defaults and notes. New
providers for an already supported protocol are added here (or in
``data/providers.local.json`` with the same schema) – no change to memory,
renderer or planner is needed (see PROVIDER_ADAPTER_GUIDE.md).

Nothing in this file assumes a brand from a string match at runtime: the
preset id is chosen by the user when creating a connection.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, Field

Protocol = Literal["chat_completions", "responses", "anthropic_messages", "llama_local"]
AuthMethod = Literal["none", "api_key", "oauth_siwc"]
Execution = Literal["local", "lan", "cloud", "unknown"]


class CapabilityDefaults(BaseModel):
    text: Literal["declared", "verified", "unknown", "no"] = "declared"
    vision: Literal["declared", "verified", "unknown", "no"] = "unknown"
    tools: Literal["declared", "verified", "unknown", "no"] = "unknown"
    structured_output: Literal["declared", "verified", "unknown", "no"] = "unknown"
    streaming: Literal["declared", "verified", "unknown", "no"] = "declared"
    reasoning: Literal["declared", "verified", "unknown", "no"] = "unknown"
    notes_de: str = ""


class ProviderPreset(BaseModel):
    id: str
    name: str
    protocol: Protocol
    base_url: str = ""  # default; user may override (own servers)
    base_url_editable: bool = False
    auth_methods: list[AuthMethod] = Field(default_factory=lambda: ["api_key"])
    execution: Execution = "cloud"
    models_path: str = "models"
    completions_path: str = "chat/completions"
    param_allowlist: Optional[list[str]] = None
    extra: dict = Field(default_factory=dict)  # adapter extras (reasoning_param, store_false, vision, …)
    capabilities: CapabilityDefaults = Field(default_factory=CapabilityDefaults)
    key_url: str = ""  # where the user gets a key
    docs_url: str = ""
    subscription_note_de: str = ""  # how account/subscription access relates (never implied)
    free_access_check: bool = True  # whether the model list is a free access check
    data_note_de: str = ""
    enabled: bool = True
    sort: int = 100


_CC_ALLOWED = ["temperature", "max_tokens", "stream", "tools", "tool_choice", "response_format", "stop", "stream_options"]

BUILTIN: list[ProviderPreset] = [
    ProviderPreset(
        id="llama_local",
        name="Eingebettetes llama.cpp (Standard)",
        protocol="llama_local",
        execution="local",
        auth_methods=["none"],
        capabilities=CapabilityDefaults(text="verified", vision="verified", tools="verified", structured_output="verified", streaming="verified", reasoning="declared", notes_de="Fähigkeiten je Modell aus dem GGUF-Header und den Echtläufen (MODEL_COMPATIBILITY.md)."),
        data_note_de="Verarbeitung ausschließlich auf diesem Rechner.",
        sort=0,
    ),
    ProviderPreset(
        id="openai_compatible",
        name="Eigener Server (OpenAI-kompatibel)",
        protocol="chat_completions",
        base_url="",
        base_url_editable=True,
        auth_methods=["none", "api_key"],
        execution="unknown",
        param_allowlist=_CC_ALLOWED,
        extra={"vision": True},
        capabilities=CapabilityDefaults(text="declared", notes_de="Fähigkeiten hängen vom Server ab; Verbindungstest prüft Erreichbarkeit, Zugang, Modellliste und eine Probe-Antwort."),
        data_note_de="Ausführungsort laut deiner Angabe (lokal, LAN oder Cloud). Ein lokal erreichbarer Server kann selbst an eine Cloud weiterleiten.",
        sort=10,
    ),
    ProviderPreset(
        id="ollama",
        name="Ollama (bereits laufend)",
        protocol="chat_completions",
        base_url="http://127.0.0.1:11434/v1",
        base_url_editable=True,
        auth_methods=["none", "api_key"],
        execution="local",
        param_allowlist=["temperature", "max_tokens", "stream", "tools", "response_format", "stop", "stream_options"],
        extra={"vision": True, "notes": "tool_choice/n/logit_bias nicht unterstützt; Bilder nur base64"},
        capabilities=CapabilityDefaults(text="declared", vision="declared", tools="declared", structured_output="declared", notes_de="Modelle mit Suffix ':cloud' werden in der Ollama-Cloud verarbeitet, nicht lokal."),
        docs_url="https://docs.ollama.com/api/openai-compatibility",
        data_note_de="Lokale Modelle laufen auf diesem Rechner; Cloud-Modelle (':cloud') nicht.",
        sort=20,
    ),
    ProviderPreset(
        id="lmstudio",
        name="LM Studio (bereits laufend)",
        protocol="chat_completions",
        base_url="http://127.0.0.1:1234/v1",
        base_url_editable=True,
        auth_methods=["none", "api_key"],
        execution="local",
        param_allowlist=_CC_ALLOWED,
        extra={"vision": True},
        capabilities=CapabilityDefaults(text="declared", vision="declared", tools="declared", structured_output="declared"),
        docs_url="https://lmstudio.ai/docs/developer/openai-compat",
        data_note_de="Verarbeitung durch LM Studio auf diesem Rechner.",
        sort=21,
    ),
    ProviderPreset(
        id="openai",
        name="OpenAI (eigener API-Schlüssel)",
        protocol="responses",
        base_url="https://api.openai.com/v1",
        auth_methods=["api_key"],
        execution="cloud",
        extra={"vision": True},
        capabilities=CapabilityDefaults(text="declared", vision="declared", tools="declared", structured_output="declared", reasoning="declared"),
        key_url="https://platform.openai.com/api-keys",
        docs_url="https://developers.openai.com/api/docs/guides/streaming-responses",
        subscription_note_de="Ein ChatGPT-Abo ist kein API-Schlüssel. Für die Nutzung eines ChatGPT-Plans gibt es die getrennte Karte „ChatGPT-Konto (Sign in with ChatGPT)“.",
        data_note_de="Anfragen gehen an OpenAI; Abrechnung über den API-Schlüssel.",
        sort=30,
    ),
    ProviderPreset(
        id="openai_siwc",
        name="ChatGPT-Konto (Sign in with ChatGPT, optional)",
        protocol="responses",
        base_url="https://api.openai.com/v1",
        auth_methods=["oauth_siwc"],
        execution="cloud",
        param_allowlist=["tools", "text", "reasoning", "instructions"],
        extra={"vision": True, "store_false": True, "request_defaults": {"store": False}},
        capabilities=CapabilityDefaults(text="declared", vision="declared", tools="declared", structured_output="unknown", reasoning="unknown", notes_de="Vorschau laut OpenAI-Dokumentation: store=false, stream=true, keine Temperatur/max_output_tokens, keine gehosteten Werkzeuge."),
        docs_url="https://developers.openai.com/siwc/token-sharing-open-source",
        subscription_note_de="Nutzt den ChatGPT-Plan des angemeldeten Kontos für Responses-Anfragen; kein Zugriff auf ChatGPT-Gespräche; Limits des Plans gelten; kein stiller Wechsel auf API-Key-Abrechnung.",
        free_access_check=True,
        data_note_de="Anfragen gehen an OpenAI unter dem angemeldeten ChatGPT-Konto.",
        sort=31,
    ),
    ProviderPreset(
        id="anthropic",
        name="Anthropic Claude (eigener API-Schlüssel)",
        protocol="anthropic_messages",
        base_url="https://api.anthropic.com/v1",
        auth_methods=["api_key"],
        execution="cloud",
        extra={"vision": True, "anthropic_version": "2026-10-21"},
        capabilities=CapabilityDefaults(text="declared", vision="declared", tools="declared", structured_output="unknown", reasoning="declared"),
        key_url="https://console.anthropic.com/settings/keys",
        docs_url="https://platform.claude.com/docs/en/build-with-claude/streaming",
        subscription_note_de="Ein Claude-Abo (claude.ai) ist kein API-Zugang; es gibt keinen offiziell dokumentierten Abo-Zugang für Drittanwendungen – daher nur API-Schlüssel.",
        data_note_de="Anfragen gehen an Anthropic.",
        sort=40,
    ),
    ProviderPreset(
        id="gemini",
        name="Google Gemini (eigener API-Schlüssel, Kompatibilitätsschicht)",
        protocol="chat_completions",
        base_url="https://generativelanguage.googleapis.com/v1beta/openai",
        auth_methods=["api_key"],
        execution="cloud",
        param_allowlist=["temperature", "max_tokens", "stream", "tools", "tool_choice", "response_format", "stop", "stream_options", "reasoning_effort"],
        extra={"vision": True, "reasoning_param": {"field": "reasoning_effort"}},
        capabilities=CapabilityDefaults(text="declared", vision="declared", tools="declared", structured_output="declared", reasoning="declared", notes_de="OpenAI-Kompatibilitätsschicht (Beta): Thinking bei Gemini 2.5 Pro/3/4 nicht abschaltbar; Teilunterstützung."),
        key_url="https://aistudio.google.com/apikey",
        docs_url="https://aistudio.google.com/docs/openai",
        subscription_note_de="Ein Gemini-App-Abo ist kein API-Zugang.",
        data_note_de="Anfragen gehen an Google.",
        sort=41,
    ),
    ProviderPreset(
        id="deepseek",
        name="DeepSeek API",
        protocol="chat_completions",
        base_url="https://api.deepseek.com",
        auth_methods=["api_key"],
        execution="cloud",
        param_allowlist=["temperature", "max_tokens", "stream", "tools", "tool_choice", "response_format", "stop", "stream_options", "thinking"],
        extra={"vision": False, "reasoning_passthrough": True, "reasoning_param": {"field": "thinking", "on": {"type": "enabled"}, "off": {"type": "disabled"}}},
        capabilities=CapabilityDefaults(text="declared", vision="no", tools="declared", structured_output="declared", reasoning="declared", notes_de="Thinking standardmäßig an; mit Werkzeugen muss reasoning_content zurückgesendet werden (im Adapter umgesetzt); temperature im Thinking-Modus wirkungslos."),
        key_url="https://platform.deepseek.com/api_keys",
        docs_url="https://api-docs.deepseek.com/guides/thinking_mode/",
        data_note_de="Anfragen gehen an DeepSeek.",
        sort=42,
    ),
    ProviderPreset(
        id="zai",
        name="Z.ai / GLM (Standard-API)",
        protocol="chat_completions",
        base_url="https://api.z.ai/api/paas/v4",
        auth_methods=["api_key"],
        execution="cloud",
        param_allowlist=_CC_ALLOWED,
        extra={"vision": True},
        capabilities=CapabilityDefaults(text="declared", vision="unknown", tools="declared", structured_output="unknown", reasoning="unknown"),
        key_url="https://z.ai/manage-apikey/apikey-list",
        docs_url="https://docs.z.ai/guides/overview/quick-start",
        subscription_note_de="Der GLM Coding Plan nutzt einen gesonderten Endpunkt und wird hier nicht als Ersatz der Standard-API verwendet.",
        data_note_de="Anfragen gehen an Z.ai.",
        sort=43,
    ),
    ProviderPreset(
        id="mistral",
        name="Mistral API",
        protocol="chat_completions",
        base_url="https://api.mistral.ai/v1",
        auth_methods=["api_key"],
        execution="cloud",
        param_allowlist=_CC_ALLOWED,
        extra={"vision": True},
        capabilities=CapabilityDefaults(text="declared", vision="declared", tools="declared", structured_output="declared"),
        key_url="https://console.mistral.ai/api-keys",
        docs_url="https://docs.mistral.ai/inference/conversations/chat-completion",
        data_note_de="Anfragen gehen an Mistral.",
        sort=44,
    ),
    ProviderPreset(
        id="openrouter",
        name="OpenRouter (optionaler Aggregator)",
        protocol="chat_completions",
        base_url="https://openrouter.ai/api/v1",
        auth_methods=["api_key"],
        execution="cloud",
        param_allowlist=_CC_ALLOWED + ["provider"],
        extra={"vision": True, "static_headers": {"HTTP-Referer": "https://github.com/Spaider-studio/spaider", "X-OpenRouter-Title": "SpAIder Foldable"}, "extra_allowed": ["provider"]},
        capabilities=CapabilityDefaults(text="declared", vision="declared", tools="declared", structured_output="declared", notes_de="Routing-Regeln (provider.order/only/allow_fallbacks/data_collection) werden je Verbindung ausdrücklich gesetzt; ausführender Anbieter laut Modellliste."),
        key_url="https://openrouter.ai/keys",
        docs_url="https://openrouter.ai/docs/features/provider-routing",
        data_note_de="OpenRouter leitet an den gewählten Anbieter weiter; Datenrichtlinie über data_collection=deny einschränkbar.",
        sort=50,
    ),
    ProviderPreset(
        id="huggingface",
        name="Hugging Face Inference Providers (optional)",
        protocol="chat_completions",
        base_url="https://router.huggingface.co/v1",
        auth_methods=["api_key"],
        execution="cloud",
        param_allowlist=_CC_ALLOWED,
        extra={"vision": True},
        capabilities=CapabilityDefaults(text="declared", tools="declared", structured_output="declared", notes_de="Modellliste des Routers nennt ausführenden Provider, Kontext, Preise, supports_tools/structured_output je Provider."),
        key_url="https://huggingface.co/settings/tokens",
        docs_url="https://huggingface.co/docs/inference-providers/hub-api",
        subscription_note_de="Ein HF-Token für Downloads ist eine andere Verbindung als die Inferenz über den Router.",
        data_note_de="Anfragen gehen an den jeweils ausführenden Inference Provider.",
        sort=51,
    ),
]


def builtin_presets() -> list[ProviderPreset]:
    return [p.model_copy(deep=True) for p in BUILTIN]


def load_presets(local_path: Optional[Path] = None) -> dict[str, ProviderPreset]:
    """Built-in presets plus ``data/providers.local.json`` (list of preset objects)."""
    out = {p.id: p for p in builtin_presets()}
    if local_path and local_path.exists():
        try:
            data = json.loads(local_path.read_text(encoding="utf-8"))
            for raw in data if isinstance(data, list) else data.get("presets", []):
                p = ProviderPreset.model_validate(raw)
                if p.protocol == "llama_local":
                    continue  # the embedded runtime is not user-definable
                out[p.id] = p
        except Exception:  # noqa: BLE001  (a broken local file must not block startup)
            pass
    return dict(sorted(out.items(), key=lambda kv: (kv[1].sort, kv[1].id)))
