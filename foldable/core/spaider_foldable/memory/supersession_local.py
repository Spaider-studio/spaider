"""SQLite port of ``app.services.supersession.resolve_supersession``.

The decision logic (state-vs-event gate and the conservative judge prompt) is
reused verbatim from SpAIder; only the three graph queries run against
``LocalMemoryStore`` instead of Neo4j.
"""

from __future__ import annotations

import asyncio
import logging

from app.config import settings
from app.models.schemas import GraphPayload
from app.services.supersession import _edge_text, _is_state_update, _judge

from .store import LocalMemoryStore

logger = logging.getLogger(__name__)


async def resolve_supersession_local(store: LocalMemoryStore, payload: GraphPayload, *, enabled: bool = True) -> int:
    if not enabled:
        return 0
    candidates_edges: list[tuple] = []
    for edge in payload.edges:
        if not (edge.relation and edge.source_id and edge.target_id) or edge.relation == "MENTIONS":
            continue
        new_text = _edge_text(edge.properties or {}, "", "", edge.relation)
        if new_text:
            candidates_edges.append((edge, new_text))
    if not candidates_edges:
        return 0

    unique_texts = list({t for _, t in candidates_edges})
    flags = await asyncio.gather(*[_is_state_update(t) for t in unique_texts], return_exceptions=True)
    state_map = {t: (flag is True) for t, flag in zip(unique_texts, flags)}
    state_edges = [(e, t) for (e, t) in candidates_edges if state_map.get(t)]
    if not state_edges:
        return 0

    async def _process(edge, new_text: str) -> int:
        try:
            rows = store.supersession_candidates(
                src=edge.source_id,
                tgt=edge.target_id,
                new_id=edge.id,
                max_candidates=settings.supersession_max_candidates,
            )
            candidates = [
                {"edge_id": r["edge_id"], "text": _edge_text(r["props"], r["subj"], r["obj"], r["rel"])} for r in rows
            ]
            if not candidates:
                return 0
            superseded_ids = await _judge(new_text, candidates)
            items = [c for c in candidates if c["edge_id"] in superseded_ids]
            if not items:
                return 0
            store.mark_superseded(items, edge.id)
            store.neutralize(items)
            logger.info("supersession: edge %s superseded %d prior fact(s)", edge.id, len(items))
            return len(items)
        except Exception as exc:  # noqa: BLE001
            logger.warning("supersession pass failed for edge %s: %s", getattr(edge, "id", "?"), exc)
            return 0

    counts = await asyncio.gather(*[_process(e, t) for e, t in state_edges])
    return sum(counts)
