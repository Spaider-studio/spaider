"""OpenAI-compatible client for the local llama-server (chat streaming,
embeddings, tokenize). Cancellation = closing the response: llama-server stops
generating when the client disconnects.

Server features used (verified against the bundled build b12032, see
tools/server/README.md of that tag): ``cache_prompt`` (KV reuse of the common
prefix), ``return_progress`` (``prompt_progress`` with ``total``/``cache``/
``processed`` during prompt processing), ``stream_options.include_usage`` and
``timings`` in the final chunk, ``chat_template_kwargs`` for models whose
template exposes a reasoning toggle, ``reasoning_format=deepseek`` server-side
so reasoning arrives separately as ``reasoning_content``.

One ``httpx.AsyncClient`` is kept per server (lazily, per event loop). Creating
a client per call cost ~400 ms on Windows (TLS context setup, measured on the
development machine) and showed up as "prompt_build" and connect latency in
every answer; the loopback connection never uses TLS, so ``verify=False``.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from typing import AsyncIterator, Optional

import httpx

logger = logging.getLogger(__name__)


@dataclass
class ChatChunk:
    text: str = ""
    reasoning: str = ""
    done: bool = False
    usage: dict = field(default_factory=dict)
    finish_reason: Optional[str] = None
    progress: Optional[dict] = None  # {"total": n, "cache": n, "processed": n, "time_ms": ms}


def loopback_async_client(**kwargs) -> httpx.AsyncClient:
    """AsyncClient for plain-HTTP loopback calls without the TLS setup cost."""
    kwargs.setdefault("trust_env", False)
    kwargs.setdefault("verify", False)
    return httpx.AsyncClient(**kwargs)


class LocalLLMClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key
        self._client: Optional[httpx.AsyncClient] = None
        self._loop: Optional[asyncio.AbstractEventLoop] = None

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    def _conn(self) -> httpx.AsyncClient:
        loop = asyncio.get_running_loop()
        if self._client is None or self._loop is not loop or self._client.is_closed:
            self._client = loopback_async_client(
                timeout=httpx.Timeout(30.0, connect=5.0),
                limits=httpx.Limits(max_connections=4, max_keepalive_connections=2),
            )
            self._loop = loop
        return self._client

    async def aclose(self) -> None:
        client, self._client = self._client, None
        if client is not None and not client.is_closed:
            try:
                await client.aclose()
            except Exception:  # noqa: BLE001
                pass

    async def health(self) -> bool:
        try:
            r = await self._conn().get(f"{self.base_url}/health", headers=self._headers(), timeout=3.0)
            return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def props(self) -> Optional[dict]:
        try:
            r = await self._conn().get(f"{self.base_url}/props", headers=self._headers(), timeout=5.0)
            return r.json() if r.status_code == 200 else None
        except (httpx.HTTPError, ValueError):
            return None

    async def tokenize_count(self, text: str) -> Optional[int]:
        try:
            r = await self._conn().post(
                f"{self.base_url}/tokenize", headers=self._headers(), json={"content": text}, timeout=30.0
            )
            if r.status_code == 200:
                return len(r.json().get("tokens", []))
        except (httpx.HTTPError, ValueError):
            pass
        return None

    async def embeddings(self, texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
        if not texts:
            return []
        r = await self._conn().post(
            f"{self.base_url}/v1/embeddings",
            headers=self._headers(),
            json={"input": texts, "model": "local"},
            timeout=timeout,
        )
        r.raise_for_status()
        data = r.json()["data"]
        data.sort(key=lambda d: d.get("index", 0))
        return [d["embedding"] for d in data]

    async def stream_chat(
        self,
        messages: list[dict],
        *,
        temperature: float = 0.7,
        max_tokens: int = 1024,
        enable_thinking: Optional[bool] = False,
        stop_event: Optional[asyncio.Event] = None,
        cache_prompt: bool = True,
        reasoning_toggle: Optional[str] = "enable_thinking",
    ) -> AsyncIterator[ChatChunk]:
        payload: dict = {
            "model": "local",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "cache_prompt": cache_prompt,
            "return_progress": True,
        }
        if reasoning_toggle and enable_thinking is not None:
            payload["chat_template_kwargs"] = {reasoning_toggle: bool(enable_thinking)}
        t0 = time.monotonic()
        first_token_ms: Optional[int] = None
        usage: dict = {}
        async with self._conn().stream(
            "POST",
            f"{self.base_url}/v1/chat/completions",
            headers=self._headers(),
            json=payload,
            timeout=httpx.Timeout(600.0, connect=10.0),
        ) as resp:
            if resp.status_code != 200:
                body = (await resp.aread()).decode("utf-8", errors="replace")
                raise RuntimeError(f"llama-server HTTP {resp.status_code}: {body[:300]}")
            async for line in resp.aiter_lines():
                if stop_event is not None and stop_event.is_set():
                    await resp.aclose()
                    break
                if not line.startswith("data:"):
                    continue
                data = line[5:].strip()
                if data == "[DONE]":
                    break
                try:
                    obj = json.loads(data)
                except ValueError:
                    continue
                if obj.get("prompt_progress"):
                    yield ChatChunk(progress=obj["prompt_progress"])
                if "usage" in obj and obj["usage"]:
                    usage.update(obj["usage"])
                if "timings" in obj and obj["timings"]:
                    usage["timings"] = obj["timings"]
                for choice in obj.get("choices", []):
                    delta = choice.get("delta") or {}
                    text = delta.get("content") or ""
                    reasoning = delta.get("reasoning_content") or ""
                    if (text or reasoning) and first_token_ms is None:
                        first_token_ms = int((time.monotonic() - t0) * 1000)
                    if text or reasoning:
                        yield ChatChunk(text=text, reasoning=reasoning)
                    if choice.get("finish_reason"):
                        usage["finish_reason"] = choice["finish_reason"]
        if first_token_ms is not None:
            usage["time_to_first_token_ms"] = first_token_ms
        timings = usage.get("timings") or {}
        if timings.get("predicted_per_second"):
            usage["tokens_per_second"] = round(float(timings["predicted_per_second"]), 2)
        if timings.get("prompt_per_second"):
            usage["prompt_tokens_per_second"] = round(float(timings["prompt_per_second"]), 2)
        if timings.get("prompt_n") is not None:
            usage["prompt_n_processed"] = timings.get("prompt_n")
        if timings.get("cache_n") is not None:
            usage["prompt_n_cached"] = timings.get("cache_n")
        yield ChatChunk(done=True, usage=usage, finish_reason=usage.get("finish_reason"))
