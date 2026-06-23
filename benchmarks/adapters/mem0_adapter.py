"""Mem0 adapter — local Chroma vector store, OpenAI LLM/embeddings pinned.

Runs entirely on local file-based storage under ``benchmarks/.bench_data/mem0/``
(gitignored). Never touches SpAIder's Neo4j. ``mem0ai`` is imported lazily so
this module loads fine in any venv; it only resolves inside its own venv.

Ingestion stores each fact verbatim (``infer=False`` by default) so no corpus
fact is dropped or merged by Mem0's extraction step — the fairest retrieval
substrate, matching how SpAIder ingests every fact. Set ``MEM0_INFER=true`` to
run Mem0's extraction pipeline instead (documented in the write-up).
"""
from __future__ import annotations

import asyncio
import os
import time

from benchmarks.adapters.base import (
    AdapterAnswer,
    MemorySystemAdapter,
    RetrievedContext,
)

_DEFAULT_DATA_DIR = "benchmarks/.bench_data/mem0"
_NATIVE_SYSTEM = (
    "Answer the user's question using the retrieved memories below. They are "
    "the authoritative facts; answer directly and concisely from them.\n\n"
    "=== Memories ===\n{context}\n=== End memories ==="
)


class Mem0Adapter(MemorySystemAdapter):
    name = "mem0"

    def __init__(
        self,
        *,
        data_dir: str | None = None,
        model: str = "gpt-4o-mini",
        embed_model: str = "text-embedding-3-small",
        user_id: str = "bench",
        infer: bool | None = None,
    ) -> None:
        self._data_dir = os.path.abspath(
            data_dir or os.environ.get("MEM0_DATA_DIR", _DEFAULT_DATA_DIR)
        )
        self._model = model
        self._embed_model = embed_model
        self._user_id = user_id
        self._infer = (
            infer
            if infer is not None
            else os.environ.get("MEM0_INFER", "false").lower() in ("1", "true", "yes")
        )
        self._mem = None

    def _client(self):
        if self._mem is None:
            from mem0 import Memory

            os.makedirs(self._data_dir, exist_ok=True)
            config = {
                "llm": {
                    "provider": "openai",
                    "config": {"model": self._model, "temperature": 0.0},
                },
                "embedder": {
                    "provider": "openai",
                    "config": {"model": self._embed_model},
                },
                "vector_store": {
                    "provider": "chroma",
                    "config": {"collection_name": "bench", "path": self._data_dir},
                },
            }
            self._mem = Memory.from_config(config)
        return self._mem

    async def ingest(self, facts: list[dict]) -> None:
        mem = self._client()

        def _add(fact: dict) -> None:
            mem.add(
                fact["text"],
                user_id=self._user_id,
                metadata={"source": fact.get("source", "corpus")},
                infer=self._infer,
            )

        for fact in facts:
            await asyncio.to_thread(_add, fact)

    async def retrieve(self, question: str, top_k: int = 10) -> RetrievedContext:
        mem = self._client()
        # mem0 2.x: top_k (not limit); the entity scope goes in `filters`;
        # threshold=0 so we keep the full top_k rather than dropping low scores.
        res = await asyncio.to_thread(
            lambda: mem.search(
                question,
                top_k=top_k,
                filters={"user_id": self._user_id},
                threshold=0.0,
            )
        )
        items = res.get("results", []) if isinstance(res, dict) else (res or [])
        lines: list[str] = []
        titles: list[str] = []
        for it in items:
            text = (it.get("memory") or it.get("text") or "").strip()
            if text:
                lines.append(f"- {text}")
            src = (it.get("metadata") or {}).get("source")
            if src:
                titles.append(src)
        return RetrievedContext(text="\n".join(lines), titles=titles)

    async def answer_native(self, question: str, max_tokens: int) -> AdapterAnswer:
        from litellm import acompletion

        t0 = time.perf_counter()
        ctx = await self.retrieve(question)
        messages = [
            {"role": "system", "content": _NATIVE_SYSTEM.format(context=ctx.text)},
            {"role": "user", "content": question},
        ]
        resp = await acompletion(
            model=self._model, messages=messages, max_tokens=max_tokens, temperature=0.0
        )
        latency = (time.perf_counter() - t0) * 1000
        msg = resp.choices[0].message
        text = (getattr(msg, "content", "") or "").strip()
        usage = getattr(resp, "usage", None)
        ti = (getattr(usage, "prompt_tokens", 0) or 0) if usage else 0
        to = (getattr(usage, "completion_tokens", 0) or 0) if usage else 0
        return AdapterAnswer(
            text=text,
            tokens_in=ti,
            tokens_out=to,
            latency_ms=round(latency, 2),
            retrieved_titles=ctx.titles,
            retrieved_text=ctx.text,
        )
