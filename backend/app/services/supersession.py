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

# Neutralize the stale denormalized role. Extraction copies a role onto the
# entity ("Idris Kane" -> description "Chief executive"); the person is NOT
# superseded (he still exists), but that description is now stale and leaks into
# the answer. For each superseded edge's endpoint entities, clear the
# description IF it derives from the superseded fact AND no OTHER non-superseded
# FACT still mentions that entity (guard against wiping a still-supported one).
_NEUTRALIZE_CYPHER = """
UNWIND $items AS item
MATCH (a:SpaiderNode)-[r:RELATION {id: item.edge_id}]->(b:SpaiderNode)
WITH [a, b] AS ents, item.text AS ftext
UNWIND ents AS ent
WITH DISTINCT ent, ftext
WHERE ent.agent_id = $aid
  AND coalesce(ent.type, '') <> 'FACT'
  AND ent.description IS NOT NULL AND ent.description <> ''
  AND toLower(ftext) CONTAINS toLower(ent.description)
OPTIONAL MATCH (f:SpaiderNode {type: 'FACT'})-[:MENTIONS]->(ent)
  WHERE NOT coalesce(f.superseded, false)
WITH ent, count(f) AS other_facts
WHERE other_facts = 0
SET ent.description = null, ent.role_superseded = true
RETURN count(ent) AS entities_neutralized
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


async def _llm_json(prompt: str) -> str:
    """One temperature-0 completion via the configured judge model. '' on failure."""
    kwargs: dict = {
        "model": settings.supersession_judge_model or settings.litellm_model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": 0,
    }
    if settings.llm_base_url:
        kwargs["api_base"] = settings.llm_base_url
    if settings.llm_api_key:
        kwargs["api_key"] = settings.llm_api_key
    try:
        resp = await acompletion_with_retry(**kwargs)
        return resp.choices[0].message.content or ""
    except Exception as exc:  # noqa: BLE001
        logger.warning("supersession llm call failed: %s", exc)
        return ""


async def _is_state_update(new_text: str) -> bool:
    """Gate: only a CURRENT-STATE assertion can supersede a prior fact.

    This is the cheap per-fact filter that kills the bulk of false positives on
    real corpora, which are mostly EVENTS. An event (a merge, review, decision,
    hire, message, bug) never invalidates a prior fact, so it never even looks
    for supersession candidates. Conservative: unclear/parse-fail -> not a state.
    """
    prompt = (
        "Does this sentence assert a CURRENT, SINGLE-VALUED STATE or ATTRIBUTE — "
        "something with exactly one value at a time that a later fact could change "
        "(a company's current CEO/leader, its current headquarters, a person's "
        "current employer/title, a current status/version/owner)?\n"
        "Answer NO if it instead describes an EVENT or action that happened at a "
        "time (a merge, review, deployment, decision, hire, message, bug report, "
        "config change) or a multi-valued fact. Past-tense actions are events.\n\n"
        f"Sentence: {new_text}\n\n"
        'Reply ONLY: {"is_state": true} or {"is_state": false}.'
    )
    content = await _llm_json(prompt)
    try:
        m = re.search(r"\{.*\}", content, re.DOTALL)
        return bool(json.loads(m.group(0)).get("is_state", False)) if m else False
    except Exception:
        return False


async def _judge(new_text: str, candidates: list[dict]) -> set[str]:
    """Ask the LLM which candidate facts the new fact replaces. Conservative."""
    numbered = "\n".join(f'- id={c["edge_id"]}: {c["text"]}' for c in candidates)
    prompt = (
        "You maintain a knowledge graph. A NEW fact just arrived. Below are EXISTING "
        "facts that share an entity with it. Decide which existing facts (if any) the "
        "new fact makes OUTDATED. Superseding a fact DELETES it, so be very "
        "conservative.\n\n"
        "A fact is outdated ONLY when the new fact states the SAME persistent, "
        "single-valued ATTRIBUTE of the SAME entity with a CHANGED value — something "
        "where only one value can be current at a time: current CEO/leader, current "
        "headquarters/location, current employer, current job title, current status, "
        "current version, current owner.\n\n"
        "NEVER mark a fact outdated if it is:\n"
        "  - an EVENT that happened at a point in time (a merge, a review, a "
        "deployment, a meeting, a decision made on a date, a hire, a release, a bug "
        "found, a config change). Later events NEVER supersede earlier ones, even "
        "about the same person, repo, or thing.\n"
        "  - a MULTI-VALUED relation where several values coexist (products a company "
        "makes, PRs a person merged, people on a team, skills, customers, tickets).\n"
        "  - merely about the same entity without stating the same single-valued "
        "attribute.\n\n"
        "If you cannot name the single-valued attribute that changed, return nothing.\n\n"
        "Examples:\n"
        '  NEW "Priya Nair is the CEO of Zephyr" vs "Idris Kane is the CEO of Zephyr" '
        "-> outdated (current CEO changed).\n"
        '  NEW "Zephyr is now headquartered in Berlin" vs "Zephyr is headquartered in '
        'Lisbon" -> outdated (current HQ changed).\n'
        '  NEW "Sara merged PR#216" vs "Sara merged PR#205" -> NOT outdated (two '
        "separate events).\n"
        '  NEW "Zephyr makes Beam" vs "Zephyr makes Lumen" -> NOT outdated (multiple '
        "products coexist).\n"
        '  NEW "Jin reviewed PR#252" vs "Marcus reviewed PR#254" -> NOT outdated '
        "(separate reviews).\n\n"
        f"NEW fact: {new_text}\n\n"
        f"EXISTING facts:\n{numbered}\n\n"
        'Reply with ONLY JSON: {"superseded_ids": ["<id>", ...]}  (empty list if none).'
    )
    content = await _llm_json(prompt)
    return _parse_superseded_ids(content, {c["edge_id"] for c in candidates})


async def resolve_supersession(driver, agent_id: str, resolved_payload) -> int:
    """Mark prior facts that the newly written edges supersede. Returns the count.

    Fire-safe: any failure is logged and swallowed so ingest always succeeds.
    """
    if not settings.supersession_enabled:
        return 0

    total = 0
    state_cache: dict[str, bool] = {}  # per-fact "is this a state assertion?"
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
        # Gate: only a current-state assertion can supersede anything. Events
        # (the bulk of real corpora) never trigger a candidate search or judge.
        if new_text not in state_cache:
            state_cache[new_text] = await _is_state_update(new_text)
        if not state_cache[new_text]:
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
                await session.run(_NEUTRALIZE_CYPHER, items=items, aid=agent_id)
            total += len(items)
            logger.info(
                "supersession: new edge %s superseded %d prior fact(s)", edge.id, len(items),
            )
        except Exception as exc:  # noqa: BLE001
            logger.warning("supersession pass failed for edge %s: %s", getattr(edge, "id", "?"), exc)

    return total
