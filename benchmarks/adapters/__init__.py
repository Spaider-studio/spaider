"""Memory-system adapters for the head-to-head benchmark.

Each adapter wraps a competing memory/RAG system (SpAIder, Mem0, Cognee, …)
behind a common interface so the runner can score them on the same corpus,
questions and judge. See ``base.MemorySystemAdapter``.

Adapters lazy-import their heavy third-party dependency *inside* their methods,
so importing this package never pulls in mem0/cognee — each runs in its own
isolated virtualenv (see ``benchmarks/adapters/README.md``).
"""
from __future__ import annotations

from benchmarks.adapters.base import (
    AdapterAnswer,
    MemorySystemAdapter,
    RetrievedContext,
)

__all__ = [
    "AdapterAnswer",
    "MemorySystemAdapter",
    "RetrievedContext",
    "build_adapter",
    "ADAPTER_NAMES",
]

ADAPTER_NAMES = ("spaider", "mem0", "cognee")


def build_adapter(name: str, **kwargs) -> MemorySystemAdapter:
    """Construct an adapter by name, importing its module (and only its module)
    lazily so an uninstalled system never affects the others."""
    if name == "spaider":
        from benchmarks.adapters.spaider_adapter import SpaiderAdapter

        return SpaiderAdapter(**kwargs)
    if name == "mem0":
        from benchmarks.adapters.mem0_adapter import Mem0Adapter

        return Mem0Adapter(**kwargs)
    if name == "cognee":
        from benchmarks.adapters.cognee_adapter import CogneeAdapter

        return CogneeAdapter(**kwargs)
    raise ValueError(f"unknown adapter {name!r}; choose from {ADAPTER_NAMES}")
