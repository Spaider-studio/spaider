"""OpenAI-compatible client for the local llama-server (chat streaming,
embeddings, tokenize). Cancellation = closing the response: llama-server stops
generating when the client disconnects."""

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


class LocalLLMClient:
    def __init__(self, base_url: str, api_key: str):
        self.base_url = base_url.rstrip("/")
        self.api_key = api_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}

    async def health(self) -> bool:
        try:
            async with httpx.AsyncClient(timeout=3.0) as client:
                r = await client.get(f"{self.base_url}/health", headers=self._headers())
                return r.status_code == 200
        except httpx.HTTPError:
            return False

    async def tokenize_count(self, text: str) -> Optional[int]:
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                r = await client.post(f"{self.base_url}/tokenize", headers=self._headers(), json={"content": text})
                if r.status_code == 200:
                    return len(r.json().get("tokens", []))
        except (httpx.HTTPError, ValueError):
            pass
        return None

    async def embeddings(self, texts: list[str], *, timeout: float = 120.0) -> list[list[float]]:
        if not texts:
            return []
        async with httpx.AsyncClient(timeout=timeout) as client:
            r = await client.post(
                f"{self.base_url}/v1/embeddings",
                headers=self._headers(),
                json={"input": texts, "model": "local"},
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
        enable_thinking: bool = False,
        stop_event: Optional[asyncio.Event] = None,
    ) -> AsyncIterator[ChatChunk]:
        payload = {
            "model": "local",
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
            "stream": True,
            "stream_options": {"include_usage": True},
            "chat_template_kwargs": {"enable_thinking": bool(enable_thinking)},
        }
        t0 = time.monotonic()
        first_token_ms: Optional[int] = None
        usage: dict = {}
        async with httpx.AsyncClient(timeout=httpx.Timeout(600.0, connect=10.0)) as client:
            async with client.stream(
                "POST", f"{self.base_url}/v1/chat/completions", headers=self._headers(), json=payload
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
        yield ChatChunk(done=True, usage=usage, finish_reason=usage.get("finish_reason"))
