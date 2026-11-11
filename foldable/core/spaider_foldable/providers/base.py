"""Adapter interface and shared helpers (HTTP client pooling, error mapping,
credential binding to the configured origin)."""

from __future__ import annotations

import asyncio
import json
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional
from urllib.parse import urlparse

import httpx

from .events import AccessCheck, GenerationError, GenerationEvent, GenerationRequest, ModelInfo

logger = logging.getLogger(__name__)

USER_AGENT = "SpAIder-Foldable/0.4 (+https://github.com/Spaider-studio/spaider)"
PRIVATE_HOSTS = ("localhost", "127.0.0.1", "::1", "0.0.0.0")


def origin_of(url: str) -> str:
    u = urlparse(url)
    port = f":{u.port}" if u.port else ""
    return f"{u.scheme}://{(u.hostname or '').lower()}{port}"


def is_loopback_url(url: str) -> bool:
    host = (urlparse(url).hostname or "").lower()
    return host in PRIVATE_HOSTS or host.endswith(".localhost")


def is_private_url(url: str) -> bool:
    """Loopback, link-local and RFC1918 ranges (used to classify own/LAN servers)."""
    import ipaddress

    host = (urlparse(url).hostname or "").lower()
    if is_loopback_url(url):
        return True
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        return host.endswith((".local", ".lan", ".internal", ".home"))
    return ip.is_private or ip.is_link_local or ip.is_loopback


@dataclass
class AdapterConfig:
    """Everything an adapter needs; the secret is resolved by the registry and lives only here."""

    connection_id: str
    provider: str  # preset id
    base_url: str
    api_key: Optional[str] = None
    headers: dict = field(default_factory=dict)  # extra static headers from the preset
    execution: str = "cloud"  # local | lan | cloud | unknown
    allow_insecure_http: bool = False  # only for explicitly configured loopback/LAN servers
    param_allowlist: Optional[set] = None  # request fields the preset allows (None = default set)
    extra: dict = field(default_factory=dict)  # preset/user extras (e.g. openrouter provider routing)
    timeout_connect_s: float = 10.0


class ProviderAdapter(ABC):
    """One instance per connection; keeps an httpx client per event loop."""

    protocol: str = ""

    def __init__(self, cfg: AdapterConfig):
        self.cfg = cfg
        self._client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None
        self._active: set[httpx.Response] = set()
        u = urlparse(cfg.base_url)
        if u.scheme not in ("http", "https"):
            raise GenerationError("bad_request", "Die Basis-URL muss mit http:// oder https:// beginnen.")
        if u.scheme == "http" and not (is_private_url(cfg.base_url) and cfg.allow_insecure_http):
            raise GenerationError(
                "bad_request",
                "Unverschlüsselte Verbindungen sind nur zu ausdrücklich freigegebenen eigenen Servern (Loopback/LAN) erlaubt.",
            )
        self.origin = origin_of(cfg.base_url)

    # ---- HTTP plumbing ------------------------------------------------------------
    def _conn(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop or self._client.is_closed:
            self._client = httpx.AsyncClient(
                timeout=httpx.Timeout(60.0, connect=self.cfg.timeout_connect_s),
                limits=httpx.Limits(max_connections=6, max_keepalive_connections=3),
                headers={"User-Agent": USER_AGENT, **self.cfg.headers},
                trust_env=self.cfg.execution == "cloud",  # proxies only for cloud targets
                verify=not is_loopback_url(self.cfg.base_url),
                follow_redirects=False,  # never carry Authorization to a redirect target
            )
            self._loop = loop
        return self._client

    async def aclose(self) -> None:
        await self.cancel()
        client, self._client = self._client, None
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def cancel(self) -> None:
        """Close every in-flight streaming response (stops generation at the provider for
        servers that abort on disconnect; cloud providers may keep billing the partial run)."""
        for r in list(self._active):
            try:
                await r.aclose()
            except Exception:  # noqa: BLE001
                pass
        self._active.clear()

    def _auth_headers(self) -> dict:
        return {"Authorization": f"Bearer {self.cfg.api_key}"} if self.cfg.api_key else {}

    def _url(self, path: str) -> str:
        return self.cfg.base_url.rstrip("/") + "/" + path.lstrip("/")

    @staticmethod
    def map_http_error(status: int, body: str, headers: Optional[httpx.Headers] = None) -> GenerationError:
        msg = _extract_message(body)
        retry_after = None
        if headers is not None and headers.get("retry-after"):
            try:
                retry_after = float(headers.get("retry-after"))
            except ValueError:
                retry_after = None
        if status == 401:
            return GenerationError("unauthorized", "Zugang abgelehnt (401): Schlüssel fehlt, ist ungültig oder abgelaufen." + _suffix(msg), status)
        if status == 402:
            return GenerationError("payment_required", "Der Anbieter meldet fehlendes Guthaben (402)." + _suffix(msg), status)
        if status == 403:
            return GenerationError("forbidden", "Der Anbieter verweigert diese Anfrage (403): Berechtigung, Richtlinie oder Region." + _suffix(msg), status)
        if status == 404:
            return GenerationError("not_found", "Endpunkt oder Modell nicht gefunden (404)." + _suffix(msg), status)
        if status == 429:
            return GenerationError("rate_limited", "Limit erreicht (429)." + _suffix(msg), status, retryable=True, retry_after_s=retry_after)
        if status in (408, 409, 425):
            return GenerationError("server_error", f"Anfrage konnte nicht verarbeitet werden ({status})." + _suffix(msg), status, retryable=True)
        if 400 <= status < 500:
            return GenerationError("bad_request", f"Der Anbieter hat die Anfrage abgelehnt ({status})." + _suffix(msg), status)
        return GenerationError("server_error", f"Serverfehler beim Anbieter ({status})." + _suffix(msg), status, retryable=status in (500, 502, 503, 504))

    @staticmethod
    def map_transport_error(exc: Exception) -> GenerationError:
        if isinstance(exc, httpx.ConnectTimeout):
            return GenerationError("timeout", "Verbindungsaufbau zum Anbieter hat zu lange gedauert.", retryable=True)
        if isinstance(exc, httpx.ReadTimeout):
            return GenerationError("timeout", "Der Anbieter hat zu lange nicht geantwortet.", retryable=True)
        if isinstance(exc, httpx.ConnectError):
            return GenerationError("network", "Server nicht erreichbar (Adresse, Netz oder Proxy prüfen).", retryable=True, raw=str(exc)[:200])
        if isinstance(exc, (httpx.RemoteProtocolError, httpx.ReadError)):
            return GenerationError("network", "Die Verbindung wurde unterbrochen; die Antwort ist unvollständig.", retryable=True, raw=str(exc)[:200])
        if isinstance(exc, httpx.HTTPError):
            return GenerationError("network", f"Netzwerkfehler: {str(exc)[:160]}", retryable=True)
        return GenerationError("protocol", f"Unerwarteter Fehler: {str(exc)[:160]}")

    # ---- interface ----------------------------------------------------------------
    @abstractmethod
    async def list_models(self) -> list[ModelInfo]: ...

    @abstractmethod
    def generate(self, req: GenerationRequest) -> AsyncIterator[GenerationEvent]: ...

    async def check_access(self, *, probe_model: Optional[str] = None, allow_generation: bool = False) -> AccessCheck:
        """Default test: reach the model list (access), optionally one tiny generation.
        Generation is only run when the caller allows it (it may cost money)."""
        import time

        res = AccessCheck()
        t0 = time.monotonic()
        try:
            models = await self.list_models()
            res.server_reached = True
            res.access_ok = True
            res.models = models
            res.model_listed = (probe_model in {m.id for m in models}) if probe_model else (len(models) > 0)
            res.message_de = f"Server erreicht, Zugang geprüft, {len(models)} Modell(e) gelistet."
        except GenerationError as exc:
            res.server_reached = exc.code not in ("network", "timeout")
            res.access_ok = False if exc.code in ("unauthorized", "forbidden", "payment_required") else None
            res.error = exc.to_dict()
            res.message_de = exc.message_de
            res.latency_ms = int((time.monotonic() - t0) * 1000)
            return res
        res.latency_ms = int((time.monotonic() - t0) * 1000)
        if allow_generation and not probe_model and models:
            probe_model = models[0].id  # no default model chosen yet: probe the first listed one
            res.details["probe_model"] = probe_model
        if allow_generation and probe_model:
            res.answer_ok = False
            try:
                text = ""
                reasoning_chars = 0
                async for ev in self.generate(GenerationRequest(messages=[{"role": "user", "content": "Antworte nur mit OK."}], model=probe_model, max_tokens=128, temperature=0.0, timeout_s=90.0)):
                    if ev.kind == "text_delta":
                        text += ev.text
                    elif ev.kind == "reasoning_delta":
                        reasoning_chars += len(ev.text or "")
                    elif ev.kind == "error":
                        if ev.error and ev.error.code == "empty_answer" and reasoning_chars:
                            break  # reasoning-only: reported below with a specific hint
                        res.error = ev.error.to_dict() if ev.error else {"code": "unknown"}
                        res.message_de += " Probe-Antwort fehlgeschlagen: " + (ev.error.message_de if ev.error else "")
                        return res
                res.answer_ok = bool(text.strip())
                res.details["probe_answer"] = text.strip()[:80]
                if res.answer_ok:
                    res.message_de += " Probe-Antwort erhalten."
                elif reasoning_chars:
                    res.details["probe_reasoning_chars"] = reasoning_chars
                    res.message_de += " Probe lieferte nur Denktext, keinen Antworttext (Reasoning-Modell: Denkmodus auf dem Server begrenzen, z. B. llama.cpp --reasoning-budget 0, oder ein anderes Modell wählen)."
                else:
                    res.message_de += " Probe-Antwort war leer."
            except GenerationError as exc:
                res.error = exc.to_dict()
                res.message_de += " Probe-Antwort fehlgeschlagen: " + exc.message_de
        return res


def _extract_message(body: str) -> str:
    try:
        obj = json.loads(body)
    except ValueError:
        return body.strip()[:200]
    if isinstance(obj, dict):
        err = obj.get("error")
        if isinstance(err, dict):
            return str(err.get("message") or err.get("type") or "")[:200]
        if isinstance(err, str):
            return err[:200]
        return str(obj.get("message") or obj.get("detail") or "")[:200]
    return ""


def _suffix(msg: str) -> str:
    return f" ({msg})" if msg else ""


def content_to_text(content) -> str:
    """Flatten a canonical content value to plain text (used by adapters without image support)."""
    if isinstance(content, str):
        return content
    parts = []
    for p in content or []:
        if isinstance(p, dict) and p.get("type") == "text":
            parts.append(str(p.get("text") or ""))
    return "\n".join(parts)


def has_images(messages: list[dict]) -> bool:
    for m in messages:
        c = m.get("content")
        if isinstance(c, list) and any(isinstance(p, dict) and p.get("type") == "image_url" for p in c):
            return True
    return False
