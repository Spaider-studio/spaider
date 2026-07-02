"""
Ingest-time semantic supersession.

When a newly ingested fact updates a functional relationship (a company's current
CEO, a headquarters, a person's employer), the prior fact is now stale but SpAIder
keeps both, so retrieval returns the contradiction and the reader guesses. This
module resolves that at ingest:

  1. For each new RELATION edge, find CANDIDATE prior edges structurally: same
     relation type, sharing exactly one endpoint (so "same attribute, changed
     value" is possible). This is a narrow, cheap Cypher filter.
  2. An LLM JUDGE decides which candidates the new fact actually replaces. This is
     the semantic step that a purely structural rule cannot do: SpAIder's
     predicates are coarse (both "X is CEO" and "Y works here" extract as
     HAS_ROLE), so structure alone would wrongly supersede every employee. The
     judge only marks a fact stale when it is the SAME single-valued attribute
     with a changed value, never a coexisting multi-valued fact.
  3. Mark the superseded edge AND its FACT node (the raw sentence, retrievable by
     embedding) so neither leaks into retrieval.

Off by default (settings.supersession_enabled). One LLM call per new edge that
has a candidate; edges with no candidate cost nothing.
"""
from __future__ import annotations

import json
import logging
import re

from app.config import settings
from app.lib.litellm_retry import acompletion_with_retry

logger = logging.getLogger(__name__)


# Candidates share an entity endpoint with the new edge (either end), regardless
# of the relation TYPE. This is deliberately broad: SpAIder's predicates are
# inconsistent ("headquartered in" vs "relocated to"), so requiring the same
# relation misses real updates. The LLM judge does the discrimination; this query
# only narrows to "facts about a shared entity" and caps the count.
_CANDIDATE_CYPHER = """
MATCH (a:SpaiderNode {agent_id: $aid})-[r:RELATION]->(b:SpaiderNode {agent_id: $aid})
WHERE NOT coalesce(r.superseded, false)
  AND r.id <> $new_id
  AND coalesce(r.relation, '') <> 'MENTIONS'
  AND (a.id IN [$src, $tgt] OR b.id IN [$src, $tgt])
RETURN r.id AS edge_id, a.label AS subj, coalesce(r.relation, 'RELATED') AS rel,
       b.label AS obj, coalesce(r.properties, '{}') AS props
LIMIT $max
"""

# Mark superseded edges, then the FACT nodes whose verbatim text matches a
# superseded edge's source sentence (so the stale text stops being retrievable).
_MARK_CYPHER = """
UNWIND $items AS item
MATCH ()-[r:RELATION {id: item.edge_id}]->()
SET r.superseded = true, r.superseded_by = $new_id
WITH collect(item.text) AS texts
MATCH (f:SpaiderNode {agent_id: $aid, type: 'FACT'})
WHERE any(t IN texts WHERE t <> '' AND f.description CONTAINS t)
SET f.superseded = true, f.superseded_by = $new_id
RETURN count(f) AS facts_marked
"""


def _edge_text(props, subj: str, obj: str, rel: str) -> str:
    """Best available human sentence for an edge: source_text > description > triple."""
    if isinstance(props, str):
        try:
            props = json.loads(props)
        except Exception:
            props = {}
    props = props or {}
    return (props.get("source_text") or props.get("description") or f"{subj} {rel} {obj}").strip()


def _parse_superseded_ids(content: str, valid: set[str]) -> set[str]:
    try:
        m = re.search(r"\{.*\}", content or "", re.DOTALL)
        data = json.loads(m.group(0)) if m else {}
        return {i for i in data.get("superseded_ids", []) if i in valid}
    except Exception:
        return set()


async def _judge(new_text: str, candidates: list[dict]) -> set[str]:
    """Ask the LLM which candidate facts the new fact replaces. Conservative."""
    numbered = "\n".join(f'- id={c["edge_id"]}: {c["text"]}' for c in candidates)
    prompt = (
        "A knowledge graph just received a NEW fact. Below are EXISTING facts that "
        "share an entity and relation type with it. Mark an existing fact OUTDATED "
        "ONLY IF the new fact REPLACES it: they state the same single-valued "
        "attribute of the same entity with a changed value (e.g. a company's current "
        "CEO, a headquarters city, a person's current employer). Do NOT mark it "
        "outdated if both facts can be true at once (multiple products, multiple "
        "employees, multiple skills, different kinds of location). When unsure, do "
        "not mark it.\n\n"
        f"NEW fact: {new_text}\n\n"
        f"EXISTING facts:\n{numbered}\n\n"
        'Reply with ONLY JSON: {"superseded_ids": ["<id>", ...]}  (empty list if none).'
    )
    kwargs: dict = {
        "model": settings.litellm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if settings.llm_base_url:
        kwargs["api_base"] = settings.llm_base_url
    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key
    try:
        resp = await acompletion_with_retry(**kwargs)
        content = resp.choices[0].message.content
        return _parse_superseded_ids(content, {c["edge_id"] for c in candidates})
    except Exception as exc:  # noqa: BLE001 — supersession must never break ingest
        logger.warning("supersession judge failed: %s", exc)
        return set()


async def resolve_supersession(driver, agent_id: str, resolved_payload) -> int:
    """Mark prior facts that the newly written edges supersede. Returns the count.

    Fire-safe: any failure is logged and swallowed so ingest always succeeds.
    """
    if not settings.supersession_enabled:
        return 0

    total = 0
    for edge in getattr(resolved_payload, "edges", []) or []:
        rel = getattr(edge, "relation", None)
        src = getattr(edge, "source_id", None)
        tgt = getattr(edge, "target_id", None)
        if not (rel and src and tgt):
            continue
        # MENTIONS links a FACT node to its entities; it is not a knowledge
        # relation and must never be superseded.
        if rel == "MENTIONS":
            continue
        new_text = _edge_text(getattr(edge, "properties", None) or {}, "", "", rel)
        if not new_text:
            continue
        try:
            async with driver.session() as session:
                result = await session.run(
                    _CANDIDATE_CYPHER,
                    aid=agent_id, src=src, tgt=tgt, new_id=edge.id,
                    max=settings.supersession_max_candidates,
                )
                candidates = [
                    {
                        "edge_id": r["edge_id"],
                        "text": _edge_text(r["props"], r["subj"], r["obj"], r["rel"]),
                    }
                    async for r in result
                ]
            if not candidates:
                continue

            superseded_ids = await _judge(new_text, candidates)
            if not superseded_ids:
                continue

            items = [
                {"edge_id": c["edge_id"], "text": c["text"]}
                for c in candidates if c["edge_id"] in superseded_ids
            ]
            async with driver.session() as session:
                await session.run(_MARK_CYPHER, items=items, new_id=edge.id, aid=agent_id)
            total += len(items)
            logger.info(
                "supersession: new edge %s superseded %d prior fact(s)", edge.id, len(items),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("supersession pass failed for edge %s: %s", getattr(edge, "id", "?"), exc)

    return total
