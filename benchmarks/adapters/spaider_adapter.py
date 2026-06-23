"""SpAIder adapter — retrieval + native answer via the ``spaider.query`` MCP tool.

``spaider.query`` returns a single text payload with ``\\n\\n``-delimited sections:

    Direct answer: <span>
    Answer:\\n<prose>
    Confidence: ...
    Top supporting facts:\\n- ...
    Top supporting entities: ...
    Node IDs (for feedback): ...
    Backend tokens: in=N out=M

* **retrieve** returns the supporting *evidence* (facts + entities) only — NOT
  SpAIder's own synthesised answer — so the shared fixed reader does the
  answering on equal footing with the other systems.
* **answer_native** returns SpAIder's own answer (``Direct answer`` preferred),
  and counts the ``Backend tokens`` trailer as the native generation cost.

Seeding is done separately via ``python -m benchmarks.seed`` (same
``spaider.ingest_fact`` path); this adapter only reads.
"""
from __future__ import annotations

import os
import re
import time

from benchmarks.adapters.base import (
    AdapterAnswer,
    MemorySystemAdapter,
    RetrievedContext,
)

_BACKEND_TOKENS_RE = re.compile(r"in=(\d+)\s+out=(\d+)")


def _split_sections(payload: str) -> list[str]:
    return [s for s in payload.split("\n\n") if s.strip()]


def _section_body(payload: str, header: str) -> str:
    """Body of the section whose first line starts with ``header`` (header
    stripped). Empty string when the section is absent."""
    for sec in _split_sections(payload):
        if sec.startswith(header):
            return sec[len(header):].lstrip(": \n")
    return ""


class SpaiderAdapter(MemorySystemAdapter):
    name = "spaider"

    def __init__(self, *, api_key: str | None = None, mcp_url: str | None = None) -> None:
        self._api_key = api_key or os.environ.get("SPAIDER_API_KEY")
        if not self._api_key:
            raise RuntimeError(
                "SPAIDER_API_KEY required for the SpAIder adapter. "
                "Run scripts/dev/setup_mcp_dev_agent.sh to provision one."
            )
        self._mcp_url = mcp_url or os.environ.get(
            "SPAIDER_MCP_URL", "http://localhost:8001/api/v1/mcp/sse"
        )

    async def _call_query(self, question: str, top_k: int = 10) -> str:
        from mcp import ClientSession
        from mcp.client.sse import sse_client

        headers = {"Authorization": f"Bearer {self._api_key}"}
        async with sse_client(self._mcp_url, headers=headers) as streams:
            async with ClientSession(*streams) as session:
                await session.initialize()
                res = await session.call_tool(
                    "spaider.query", {"question": question, "top_k": top_k}
                )
                return "\n".join(getattr(c, "text", str(c)) for c in res.content)

    async def ingest(self, facts: list[dict]) -> None:
        raise NotImplementedError(
            "Seed SpAIder with `python -m benchmarks.seed --corpus <yaml>` "
            "(same spaider.ingest_fact path). The adapter only reads."
        )

    async def retrieve(self, question: str, top_k: int = 10) -> RetrievedContext:
        payload = await self._call_query(question, top_k)
        facts = _section_body(payload, "Top supporting facts:")
        entities = _section_body(payload, "Top supporting entities:")
        parts = [p for p in (facts, entities) if p]
        return RetrievedContext(text="\n".join(parts))

    async def answer_native(self, question: str, max_tokens: int) -> AdapterAnswer:
        t0 = time.perf_counter()
        payload = await self._call_query(question)
        latency = (time.perf_counter() - t0) * 1000
        answer = _section_body(payload, "Direct answer:") or _section_body(payload, "Answer:")
        bt_in = bt_out = 0
        for sec in _split_sections(payload):
            if sec.startswith("Backend tokens:"):
                m = _BACKEND_TOKENS_RE.search(sec)
                if m:
                    bt_in, bt_out = int(m.group(1)), int(m.group(2))
        return AdapterAnswer(
            text=answer.strip(),
            tokens_in=bt_in,
            tokens_out=bt_out,
            latency_ms=round(latency, 2),
            retrieved_text=payload,
        )
