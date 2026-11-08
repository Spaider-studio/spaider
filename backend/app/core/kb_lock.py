"""One writer per knowledge base inside this process.

Resolving entities and writing them must not interleave: two concurrent ingests
that mention a new entity would both find nothing and both create it.
"""
from __future__ import annotations

import asyncio
from collections import OrderedDict

_MAX_TRACKED = 1024
_locks: "OrderedDict[str, asyncio.Lock]" = OrderedDict()


def kb_write_lock(agent_id: str) -> asyncio.Lock:
    lock = _locks.get(agent_id)
    if lock is None:
        lock = asyncio.Lock()
        _locks[agent_id] = lock
    _locks.move_to_end(agent_id)
    while len(_locks) > _MAX_TRACKED:
        oldest, candidate = next(iter(_locks.items()))
        if candidate.locked() or candidate is lock:
            break
        del _locks[oldest]
    return lock
