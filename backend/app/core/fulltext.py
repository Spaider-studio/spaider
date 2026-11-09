"""Lucene helpers for the shared full-text index.

The index spans every KB. Without a KB clause, the top hits can all belong to a
busy neighbour, leaving a small KB with nothing after filtering.
"""
from __future__ import annotations

from collections.abc import Iterable


def _quoted(value: str) -> str:
    return '"' + value.replace("\\", "\\\\").replace('"', '\\"') + '"'


def scoped(search: str, kbs: Iterable[str]) -> str:
    """Limit a Lucene query to the given KBs."""
    clause = " OR ".join(f"agent_id:{_quoted(kb)}" for kb in kbs)
    return f"({clause}) AND ({search})"
