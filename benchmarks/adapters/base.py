"""Common interface every memory-system adapter implements.

The benchmark compares systems on two answer modes:

* **fixed-reader** — the adapter only *retrieves* (``retrieve``); the runner then
  feeds the retrieved context to a single shared reader model. Isolates the
  memory's contribution, since every system shares the same reader.
* **native** — the adapter answers end-to-end its own way (``answer_native``),
  measuring the product as shipped.

Adapters MUST lazy-import their third-party dependency inside their methods so
that importing the package is cheap and dependency-free (each system lives in
its own venv).
"""
from __future__ import annotations

import abc
from dataclasses import dataclass, field


@dataclass
class RetrievedContext:
    """What an adapter's ``retrieve`` hands to the shared fixed-reader."""

    text: str
    """Joined retrieved passages, ready to drop into the reader prompt."""
    titles: list[str] = field(default_factory=list)
    """Source identifiers / titles of the retrieved items. Feeds the
    orthogonal ``retrieval_hit`` metric (did the gold passages surface?)."""


@dataclass
class AdapterAnswer:
    """What an adapter's ``answer_native`` returns end-to-end."""

    text: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: float = 0.0
    retrieved_titles: list[str] = field(default_factory=list)
    retrieved_text: str | None = None
    """Retrieved context the system used, when the native path exposes it —
    so ``retrieval_hit`` can be computed for native runs too. ``None`` when the
    system doesn't surface it."""


class MemorySystemAdapter(abc.ABC):
    """Adapter contract. ``name`` is the arm label used in the JSONL ``mode``
    column (e.g. ``mem0`` → rows ``mem0-fixed`` / ``mem0-native``)."""

    name: str = "base"

    @abc.abstractmethod
    async def ingest(self, facts: list[dict]) -> None:
        """Load a corpus. ``facts`` are the neutral ingest dicts produced by
        ``benchmarks.seed._load_corpus`` — ``{task, text, source}`` where
        ``text`` is already ``[date] [type] <fact>``."""

    @abc.abstractmethod
    async def retrieve(self, question: str, top_k: int = 10) -> RetrievedContext:
        """Retrieve relevant context for the fixed-reader path."""

    @abc.abstractmethod
    async def answer_native(self, question: str, max_tokens: int) -> AdapterAnswer:
        """Answer end-to-end using the system's own QA/synthesis."""

    async def aclose(self) -> None:
        """Optional cleanup (close clients, flush stores). No-op by default."""
        return None
