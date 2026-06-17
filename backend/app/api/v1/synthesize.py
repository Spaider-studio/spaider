"""
Synthesize API endpoints: generate LLM fine-tuning datasets from the knowledge graph.

Endpoints
---------
POST /api/v1/synthesize                    → LLM-assisted strategy synthesis
GET  /api/v1/synthesize/export             → streaming ChatML .jsonl export (passthrough)
GET  /api/v1/synthesize/generate_dataset   → Teacher-LLM agentic tool-call trajectory synthesis
GET  /api/v1/synthesize/download/:id       → download a previously generated file

Route order matters: literal paths (/export, /generate_dataset) must be declared
before the wildcard (/download/{dataset_id}) to prevent FastAPI misrouting.
"""
from __future__ import annotations

import asyncio
import json
import logging
import uuid
from pathlib import Path
from typing import AsyncGenerator, Optional

from fastapi import APIRouter, Depends, Header, HTTPException, Query
from fastapi.responses import FileResponse, StreamingResponse

from app.config import settings
from app.lib.litellm_retry import acompletion_with_retry
from app.models.requests import SynthesizeRequest
from app.models.schemas import SynthesizeResult
from app.services.auth_service import AuthService

# Service-module SynthesizeConfig — distinct from app.models.schemas.SynthesizeConfig
# (which is the older API-layer DTO). Aliased on import so the dual-class
# situation is obvious to future readers.
from app.services.synthesizer import SynthesizeConfig as ServiceSynthesizeConfig

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# B3 — Request → service config strategy-name translation.
#
# The API surface accepts singular ``strategy`` ("factual" | "reasoning" |
# "relations") for backward compat; the service expects a list of
# canonical strategy keys ("factual_qa" | "reasoning_chains" |
# "relation_extraction"). Unknown values fall back to the full default
# strategy set so the endpoint never silently produces an empty dataset
# for a misspelled strategy name.
# ---------------------------------------------------------------------------
_STRATEGY_NAME_MAP: dict[str, str] = {
    "factual":    "factual_qa",
    "reasoning":  "reasoning_chains",
    "relations":  "relation_extraction",
}

# B3 — Output format translation. The API contract permits "both" as a
# legacy value; the service supports only "openai" | "alpaca". "both"
# maps to "openai" because OpenAI's chat-messages format is the
# unambiguously-correct default for modern fine-tuning workflows.
_OUTPUT_FORMAT_MAP: dict[str, str] = {
    "both":    "openai",
    "openai":  "openai",
    "alpaca":  "alpaca",
}


# ---------------------------------------------------------------------------
# B4 — Fail-closed clearance resolver.
#
# Extracts the caller's clearance level from the X-Api-Key header via
# AuthService.get_agent_by_api_key. Returns 0 (most restrictive) on ANY
# failure path — missing header, unknown key, Redis outage, malformed
# record, missing clearance_level field. Never returns a permissive
# default. Used by ``export_chatml_stream`` to gate the Cypher predicate.
#
# Scoped to this module rather than a global FastAPI dependency because
# global REST auth wiring is Pillar 2 work tracked on a separate branch;
# this is the minimum-viable security fix for the bypass identified in
# audit finding B4.
# ---------------------------------------------------------------------------
async def _resolve_caller_clearance(
    x_api_key: Optional[str] = Header(default=None, alias="X-Api-Key"),
    authorization: Optional[str] = Header(default=None),
) -> int:
    """Return the caller's clearance level in [0, 5]. Fail-closed → 0."""
    api_key: Optional[str] = x_api_key
    if not api_key and authorization:
        prefix = authorization[:7].lower()
        if prefix == "bearer ":
            api_key = authorization[7:].strip()
    if not api_key:
        # No API key supplied → fail closed.
        return 0
    try:
        auth = AuthService()
        record = await auth.get_agent_by_api_key(api_key)
    except Exception as exc:  # noqa: BLE001
        logger.warning(
            "B4: clearance resolution failed (auth lookup raised %s) "
            "— failing closed to clearance=0",
            exc,
        )
        return 0
    if not record:
        # Unknown key → fail closed.
        return 0
    raw_level = record.get("clearance_level")
    if raw_level is None:
        return 0
    try:
        level = int(raw_level)
    except (TypeError, ValueError):
        return 0
    # Clamp to the documented [1, 5] Diplomat Protocol range; treat
    # anything outside as fail-closed.
    if level < 1 or level > 5:
        return 0
    return level

# ---------------------------------------------------------------------------
# Tool definition — what the fine-tuned SLM learns to call
# ---------------------------------------------------------------------------

_QUERY_SPAIDER_TOOL: dict = {
    "type": "function",
    "function": {
        "name": "query_spaider",
        "description": (
            "Query the SpAIder knowledge graph for information about entities, "
            "relations, concepts, or multi-hop connections between topics. "
            "Use this tool whenever you need factual information from the graph."
        ),
        "parameters": {
            "type": "object",
            "properties": {
                "question": {
                    "type": "string",
                    "description": "Natural language question to search the knowledge graph with.",
                },
                "agent_id": {
                    "type": "string",
                    "description": "Optional: target a specific agent's knowledge domain.",
                },
            },
            "required": ["question"],
        },
    },
}

# ---------------------------------------------------------------------------
# Teacher LLM system prompt
# ---------------------------------------------------------------------------

_TEACHER_SYSTEM_PROMPT = """You are an expert synthetic training-data generator for agentic AI systems.

Given a set of knowledge graph facts (entities, types, and their descriptions), you will generate
a realistic multi-turn tool-use trajectory that teaches a small language model to query the
SpAIder knowledge graph autonomously.

You MUST respond with a single valid JSON object — no markdown, no prose, no code fences:

{
  "user_question": "<a natural, realistic user question that requires looking up these facts>",
  "tool_query": "<the exact search question the agent should send to query_spaider>",
  "tool_agent_id": "<the agent_id whose domain is most relevant, or null for cross-domain>",
  "simulated_result": "<a concise, factual tool response as if the graph returned these facts>",
  "final_answer": "<the agent's final answer to the user, grounded in the tool result>"
}

Rules:
- user_question must be something a real user would ask — not a robot.
- tool_query must be semantically different from user_question (rephrase/decompose it).
- simulated_result must be a condensed, factual synthesis of the provided facts.
- final_answer must cite the tool result and be at least 2 sentences.
- If facts span multiple agents, tool_agent_id should be null and final_answer must mention cross-domain synthesis.
- All fields are required. Return ONLY the JSON object."""

# ---------------------------------------------------------------------------
# Lazy singletons
# ---------------------------------------------------------------------------

_synthesizer = None
_graph_service = None


def _get_graph_service():
    global _graph_service
    if _graph_service is None:
        from app.services.graph_service import GraphService
        _graph_service = GraphService()
    return _graph_service


def _get_synthesizer():
    global _synthesizer
    if _synthesizer is None:
        from app.services.synthesizer import ModelSynthesizer
        _synthesizer = ModelSynthesizer(graph_service=_get_graph_service())
    return _synthesizer


# ---------------------------------------------------------------------------
# Pipeline helpers for generate_dataset
# ---------------------------------------------------------------------------


def _parse_props(raw) -> dict:
    """Deserialise a Neo4j properties field (JSON string or dict) safely."""
    if isinstance(raw, dict):
        return raw
    if isinstance(raw, str):
        try:
            return json.loads(raw)
        except Exception:
            pass
    return {}


def _node_record_to_fact(record) -> str:
    """Convert a single Neo4j record row into a human-readable fact string."""
    label: str = record["label"] or "Unknown"
    ntype: str = record["type"] or "Node"
    agent: str = record["agent_id"] or "unknown"
    props = _parse_props(record["properties"])
    desc: str = props.get("description") or props.get("source_text") or ""
    aliases: list = props.get("aliases") or []

    parts = [f"[{agent}] {label} ({ntype})"]
    if desc:
        parts.append(f"— {desc[:300]}")
    if aliases:
        parts.append(f"(also: {', '.join(str(a) for a in aliases[:3])})")
    return " ".join(parts)


async def _get_random_subgraph(session, agent_id: Optional[str]) -> list[str]:
    """
    Sample a random set of knowledge facts from Neo4j.

    Strategy 1 (preferred): Sample nodes from both sides of a random
    SHARES_KNOWLEDGE_WITH bridge — produces rich cross-domain examples.

    Strategy 2 (fallback): Random nodes from the target agent (or any agent).
    """
    # ── Strategy 1: swarm bridge sampling ────────────────────────────────
    bridge_cypher = """
        MATCH (sa:SystemAgent)-[:SHARES_KNOWLEDGE_WITH]->(sb:SystemAgent)
        WITH sa, sb ORDER BY rand() LIMIT 1
        MATCH (n:SpaiderNode)
        WHERE (n.agent_id = sa.agent_id OR n.agent_id = sb.agent_id)
          AND NOT n:SystemAgent
          AND n.properties IS NOT NULL
        WITH n ORDER BY rand() LIMIT 8
        RETURN
            n.label      AS label,
            n.type       AS type,
            n.properties AS properties,
            n.agent_id   AS agent_id
    """
    result = await session.run(bridge_cypher)
    records = await result.data()

    if records:
        return [_node_record_to_fact(r) for r in records]

    # ── Strategy 2: single-agent / full-multiverse fallback ───────────────
    if agent_id:
        fallback_cypher = """
            MATCH (n:SpaiderNode {agent_id: $agent_id})
            WHERE NOT n:SystemAgent AND n.properties IS NOT NULL
            WITH n ORDER BY rand() LIMIT 8
            RETURN n.label AS label, n.type AS type,
                   n.properties AS properties, n.agent_id AS agent_id
        """
        result = await session.run(fallback_cypher, agent_id=agent_id)
    else:
        fallback_cypher = """
            MATCH (n:SpaiderNode)
            WHERE NOT n:SystemAgent AND n.properties IS NOT NULL
            WITH n ORDER BY rand() LIMIT 8
            RETURN n.label AS label, n.type AS type,
                   n.properties AS properties, n.agent_id AS agent_id
        """
        result = await session.run(fallback_cypher)

    records = await result.data()
    return [_node_record_to_fact(r) for r in records]


async def _call_teacher_llm(facts: list[str]) -> Optional[dict]:
    """
    Ask the Teacher LLM to synthesise one complete tool-call trajectory
    from the given graph facts.  Returns the parsed JSON dict or None on error.
    """
    facts_block = "\n".join(f"• {f}" for f in facts)
    try:
        response = await acompletion_with_retry(
            model=settings.litellm_model,
            api_key=settings.llm_api_key or None,
            base_url=settings.llm_base_url or None,
            messages=[
                {"role": "system", "content": _TEACHER_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": (
                        f"Generate a tool-use training example from these knowledge graph facts:\n\n"
                        f"{facts_block}"
                    ),
                },
            ],
            temperature=0.85,
            max_tokens=1024,
            response_format={"type": "json_object"},
        )
        raw: str = (response.choices[0].message.content or "").strip()
        # Strip possible markdown fences from non-compliant models
        if raw.startswith("```"):
            nl = raw.find("\n")
            raw = raw[nl + 1:] if nl != -1 else raw[3:]
            raw = raw.rstrip("`").strip()
        return json.loads(raw)
    except Exception as exc:
        logger.warning("Teacher LLM call failed: %s", exc)
        return None


def _build_trajectory(traj: dict) -> dict:
    """
    Assemble a strict OpenAI ChatML tool-call training record from the
    Teacher LLM's structured output.
    """
    call_id = f"call_{uuid.uuid4().hex[:16]}"
    tool_agent_id: Optional[str] = traj.get("tool_agent_id") or None

    tool_args: dict = {"question": traj["tool_query"]}
    if tool_agent_id:
        tool_args["agent_id"] = tool_agent_id

    return {
        "messages": [
            {
                "role": "system",
                "content": (
                    "You are an autonomous SpAIder agent. You have access to the "
                    "`query_spaider` tool to retrieve information from the knowledge graph. "
                    "Always use the tool before answering factual questions."
                ),
            },
            {
                "role": "user",
                "content": traj["user_question"],
            },
            {
                "role": "assistant",
                "content": None,
                "tool_calls": [
                    {
                        "id": call_id,
                        "type": "function",
                        "function": {
                            "name": "query_spaider",
                            "arguments": json.dumps(tool_args, ensure_ascii=False),
                        },
                    }
                ],
            },
            {
                "role": "tool",
                "tool_call_id": call_id,
                "content": traj["simulated_result"],
            },
            {
                "role": "assistant",
                "content": traj["final_answer"],
            },
        ],
        "tools": [_QUERY_SPAIDER_TOOL],
    }


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.post("", response_model=SynthesizeResult)
async def synthesize_dataset(request: SynthesizeRequest):
    """
    Generate a fine-tuning dataset from the knowledge graph.

    Strategies:
    - factual: simple entity-attribute Q&A pairs       → service: factual_qa
    - reasoning: multi-hop chain-of-thought examples   → service: reasoning_chains
    - relations: relation extraction training examples → service: relation_extraction

    Output formats: openai (chat JSONL), alpaca, or both (→ openai).

    B3 fix: previously this endpoint called ``synthesizer.synthesize``
    with the wrong kwargs (``nodes=``, ``edges=``, ``strategy=``,
    ``max_samples=``, ``min_path_length=``) producing a runtime
    TypeError before any caller ever reached a 500. It also read
    ``result.dataset_id`` / ``result.stats`` which don't exist on the
    service's ``SynthesizeResult``. The fix builds the service-module
    ``SynthesizeConfig`` from the request, calls with the correct
    signature, and maps the service result onto the API contract.
    """
    synthesizer = _get_synthesizer()
    graph = _get_graph_service()

    try:
        # ── Preserve original 404 contract ──────────────────────────────
        # The service refetches the graph internally, but probing here
        # lets us return the documented "Ingest data first." message
        # rather than a vacuous total_examples=0 success response.
        _payload = await graph.get_full_graph(agent_id=request.agent_id)
        if not _payload.nodes:
            raise HTTPException(
                status_code=404,
                detail=f"No nodes found for agent_id='{request.agent_id}'. Ingest data first.",
            )

        # ── Build the service config from the request ────────────────────
        canonical_strategy = _STRATEGY_NAME_MAP.get(request.strategy)
        strategies: list[str] = (
            [canonical_strategy]
            if canonical_strategy is not None
            # Unknown strategy → run the full default set rather than
            # silently emitting zero examples for a misspelled name.
            else ["factual_qa", "reasoning_chains", "relation_extraction"]
        )
        service_output_format = _OUTPUT_FORMAT_MAP.get(request.output_format, "openai")

        config = ServiceSynthesizeConfig(
            strategies=strategies,
            output_format=service_output_format,
            min_confidence=request.min_confidence,
            max_examples=request.max_samples,
            # node_types stays at its None default — the API has no field for it today.
        )

        service_result = await synthesizer.synthesize(
            agent_id=request.agent_id,
            config=config,
        )

        # ── Map service result → API SynthesizeResult ───────────────────
        # API contract (app.models.schemas.SynthesizeResult):
        #   status, dataset_id, dataset_path, stats
        # Service contract (app.services.synthesizer.SynthesizeResult):
        #   agent_id, output_path, total_examples, strategy_counts, duplicate_count
        from pathlib import Path as _Path
        dataset_id = _Path(service_result.output_path).stem or service_result.agent_id
        api_result = SynthesizeResult(
            status="completed",
            dataset_id=dataset_id,
            dataset_path=service_result.output_path,
            stats={
                "total_samples":     service_result.total_examples,
                "strategy_counts":   service_result.strategy_counts,
                "duplicate_count":   service_result.duplicate_count,
                "requested_strategy": request.strategy,
                "resolved_strategies": strategies,
            },
        )

        logger.info(
            "Synthesized dataset %s for agent=%s strategy=%s samples=%d",
            api_result.dataset_id,
            request.agent_id,
            request.strategy,
            service_result.total_examples,
        )
        return api_result

    except HTTPException:
        raise
    except Exception as exc:
        logger.exception("Error synthesizing dataset: %s", exc)
        raise HTTPException(status_code=500, detail=str(exc))


@router.get("/export")
async def export_chatml_stream(
    agent_id: Optional[str] = Query(
        default=None,
        description="Export only nodes belonging to this agent. Omit for the full multiverse.",
    ),
    caller_clearance: int = Depends(_resolve_caller_clearance),
):
    """
    Stream the entire knowledge graph (or a single agent's subset) as a
    ChatML-formatted `.jsonl` file suitable for OpenAI / LM-Studio fine-tuning.

    Each line is a complete JSON object:
        {"messages": [
            {"role": "system",  "content": "..."},
            {"role": "user",    "content": "..."},
            {"role": "assistant","content": "..."}
        ]}

    The response is streamed record-by-record from the Neo4j cursor so that
    arbitrarily large graphs never materialise fully in RAM.

    **B4 — Diplomat Protocol clearance enforcement.**
    The caller's clearance level is resolved from the X-Api-Key header
    (or Authorization: Bearer). On any auth failure — missing key,
    invalid key, Redis outage, malformed agent record — clearance
    defaults to 0 (fail-closed), which excludes every node with an
    explicit ``clearance_level`` ≥ 1. Streaming Cypher carries the
    ``coalesce(n.clearance_level, 0) <= $caller_clearance`` predicate
    as a bound parameter (never string-interpolated).
    """
    graph = _get_graph_service()
    filename = (
        f"spaider_{agent_id}_training.jsonl"
        if agent_id
        else "spaider_multiverse_training.jsonl"
    )

    async def _chatml_generator() -> AsyncGenerator[str, None]:
        """
        Pull nodes from Neo4j one at a time via an async cursor and yield
        each formatted ChatML record immediately.  Memory footprint = O(1).
        """
        # ── Cypher ────────────────────────────────────────────────────────
        # We target every SpaiderNode that is NOT a SystemAgent gravity
        # centre (those carry no knowledge text worth training on).
        # If agent_id is supplied we add a WHERE filter; otherwise we export
        # the full multiverse.  The ORDER BY keeps output deterministic.
        #
        # B4 clearance predicate is bound as $caller_clearance (parameter,
        # not literal). When the resolver failed closed (caller_clearance=0)
        # this excludes every node with an explicit clearance_level set.
        if agent_id:
            cypher = """
                MATCH (n:SpaiderNode {agent_id: $agent_id})
                WHERE NOT n:SystemAgent
                  AND coalesce(n.clearance_level, 0) <= $caller_clearance
                RETURN
                    n.id         AS id,
                    n.label      AS label,
                    n.type       AS type,
                    n.properties AS properties,
                    n.agent_id   AS agent_id
                ORDER BY n.label ASC
            """
            params: dict = {
                "agent_id":         agent_id,
                "caller_clearance": caller_clearance,
            }
        else:
            cypher = """
                MATCH (n:SpaiderNode)
                WHERE NOT n:SystemAgent AND n.agent_id IS NOT NULL
                  AND coalesce(n.clearance_level, 0) <= $caller_clearance
                RETURN
                    n.id         AS id,
                    n.label      AS label,
                    n.type       AS type,
                    n.properties AS properties,
                    n.agent_id   AS agent_id
                ORDER BY n.agent_id ASC, n.label ASC
            """
            params = {"caller_clearance": caller_clearance}

        # ── Stream ────────────────────────────────────────────────────────
        try:
            async with graph._driver.session() as session:
                result = await session.run(cypher, **params)

                async for record in result:
                    node_label: str = record["label"] or "Unknown"
                    node_type: str  = record["type"]  or "Node"
                    src_agent: str  = record["agent_id"] or "unknown"

                    # Deserialise properties (stored as JSON string in Neo4j)
                    props_raw = record["properties"]
                    try:
                        props: dict = (
                            json.loads(props_raw)
                            if isinstance(props_raw, str)
                            else (props_raw if isinstance(props_raw, dict) else {})
                        )
                    except Exception:
                        props = {}

                    # Build the richest possible assistant answer from available fields
                    description: str = props.get("description") or ""
                    source_text: str = props.get("source_text") or ""
                    aliases: list    = props.get("aliases") or []

                    answer_parts: list[str] = []
                    if description:
                        answer_parts.append(description)
                    if source_text and source_text != description:
                        answer_parts.append(source_text)
                    if aliases:
                        answer_parts.append(
                            f"Also known as: {', '.join(str(a) for a in aliases)}."
                        )

                    # Skip nodes with no usable knowledge text — they add noise
                    if not answer_parts:
                        continue

                    assistant_content = " ".join(answer_parts)

                    # ── ChatML record ─────────────────────────────────────
                    record_obj = {
                        "messages": [
                            {
                                "role": "system",
                                "content": (
                                    f"You are the {src_agent} agent on the SpAIder "
                                    f"platform. You are an expert in your knowledge "
                                    f"domain and answer precisely and factually."
                                ),
                            },
                            {
                                "role": "user",
                                "content": (
                                    f"What can you tell me about this topic: "
                                    f"{node_label} ({node_type})?"
                                ),
                            },
                            {
                                "role": "assistant",
                                "content": assistant_content,
                            },
                        ]
                    }

                    yield json.dumps(record_obj, ensure_ascii=False) + "\n"

        except Exception as exc:
            logger.exception("ChatML export stream error: %s", exc)
            # Yield a sentinel error comment so the client knows the stream broke
            yield json.dumps({"__error__": str(exc)}) + "\n"

    return StreamingResponse(
        _chatml_generator(),
        media_type="application/jsonlines",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/dpo")
async def export_dpo_stream(
    agent_id: str = Query(
        ...,
        description="Agent whose graph to export as DPO preference pairs.",
    ),
    limit: int = Query(
        default=5_000, ge=1, le=50_000,
        description="Maximum number of start-nodes to scan.",
    ),
    max_depth: int = Query(
        default=3, ge=1, le=5,
        description="Maximum graph-path depth for the reasoning chain.",
    ),
    caller_clearance: int = Depends(_resolve_caller_clearance),
):
    """
    Stream the agent's graph as **DPO preference pairs** — one
    ``{"prompt", "chosen", "rejected"}`` JSON object per line, ready for
    TRL's ``DPOTrainer`` (or any DPO-compatible trainer).

    The training signal is RLHG (Reinforcement Learning from Graph): the
    chosen side is built from high-energy paths the agent actually found
    useful in production (ACT-R ``energy_level`` + Hebbian
    ``utility_weight``), the rejected side from low-energy dead ends. No
    human labelling involved — see ``docs/finetuning-export.md``.

    **Usage-signal guardrail.** DPO needs energy *separation*, which only
    exists once the graph has been queried. A freshly-seeded agent (every
    node at energy 1.0, retrieval_count 0) yields zero pairs; rather than
    hand the caller an empty file we fail with 422 and say what to do.

    Clearance (B4 Diplomat Protocol) is resolved from the caller's API key
    and gates every node on both paths — same fail-closed posture as
    ``/export``.
    """
    # Late import: the script module configures its own logging on import,
    # which is fine, but keeping it out of module scope avoids paying that
    # cost for every other /synthesize route.
    from app.scripts.synthesizer_export import _stream_dpo_pairs

    graph = _get_graph_service()

    # ── Pre-flight: does this agent's graph have any DPO signal at all? ─────
    # Probes the real pair criteria (not a proxy) by asking for a single pair.
    probe = _stream_dpo_pairs(
        graph._driver, agent_id,
        limit=1, batch_size=1, max_depth=max_depth,
        caller_clearance=caller_clearance,
    )
    has_signal = False
    async for _ in probe:
        has_signal = True
        break
    if not has_signal:
        raise HTTPException(
            status_code=422,
            detail=(
                "No DPO pairs can be generated for this agent yet: the graph "
                "has no usage signal (energy separation). DPO needs nodes the "
                "agent has actually retrieved (chosen) and dead ends it "
                "hasn't (rejected). Ingest data, run real queries against it "
                "for a while, then export again — or use the ChatML export, "
                "which works on any graph."
            ),
        )

    async def _dpo_generator() -> AsyncGenerator[str, None]:
        try:
            async for sample in _stream_dpo_pairs(
                graph._driver, agent_id,
                limit=limit, batch_size=250, max_depth=max_depth,
                caller_clearance=caller_clearance,
            ):
                yield sample.to_jsonl_line() + "\n"
        except Exception as exc:
            logger.exception("DPO export stream error: %s", exc)
            yield json.dumps({"__error__": str(exc)}) + "\n"

    return StreamingResponse(
        _dpo_generator(),
        media_type="application/jsonlines",
        headers={
            "Content-Disposition": f'attachment; filename="spaider_{agent_id}_dpo.jsonl"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
        },
    )


@router.get("/generate_dataset")
async def generate_agentic_dataset(
    agent_id: Optional[str] = Query(
        default=None,
        description="Scope sampling to a specific agent. Omit to use the full multiverse.",
    ),
    num_samples: int = Query(
        default=50,
        ge=1,
        le=500,
        description="Number of tool-call trajectories to generate.",
    ),
    concurrency: int = Query(
        default=5,
        ge=1,
        le=20,
        description="Max simultaneous Teacher-LLM calls.",
    ),
):
    """
    Generate synthetic agentic tool-call trajectories for fine-tuning small LMs.

    Each record in the output `.jsonl` is a complete 5-turn conversation:
        system → user → assistant (tool_call) → tool (result) → assistant (final answer)

    The model is trained to autonomously call `query_spaider` when it needs
    knowledge-graph facts — mirroring real SpAIder agent behaviour.

    Sampling strategy:
      1. Try to find a random SHARES_KNOWLEDGE_WITH bridge and sample nodes
         from both connected agents — produces rich cross-domain examples.
      2. Fall back to random nodes within `agent_id` (or any agent) if no
         bridges exist.

    The Teacher LLM (configured via `LLM_MODEL` env) invents realistic user
    questions, tool queries, simulated results, and final answers.
    The stream is line-delimited JSON — safe to pipe directly to training jobs.
    """
    if not settings.llm_api_key and not settings.llm_base_url:
        raise HTTPException(
            status_code=503,
            detail=(
                "No LLM backend configured. Set LLM_API_KEY (for OpenAI/Anthropic) "
                "or LLM_BASE_URL (for local Ollama/vLLM) in your environment."
            ),
        )

    graph = _get_graph_service()
    filename = (
        f"spaider_{agent_id}_toolcall_{num_samples}.jsonl"
        if agent_id
        else f"spaider_multiverse_toolcall_{num_samples}.jsonl"
    )

    async def _pipeline_generator() -> AsyncGenerator[str, None]:
        sem = asyncio.Semaphore(concurrency)
        queue: asyncio.Queue[str] = asyncio.Queue()
        completed = 0
        total = num_samples

        async def _worker(_idx: int) -> None:
            nonlocal completed
            async with sem:
                try:
                    async with graph._driver.session() as session:
                        facts = await _get_random_subgraph(session, agent_id)

                    if not facts:
                        logger.warning("generate_dataset worker %d: no facts sampled", _idx)
                        await queue.put(
                            json.dumps({"__skipped__": f"worker_{_idx}_no_facts"}) + "\n"
                        )
                        return

                    traj_raw = await _call_teacher_llm(facts)
                    if not traj_raw:
                        await queue.put(
                            json.dumps({"__skipped__": f"worker_{_idx}_llm_failed"}) + "\n"
                        )
                        return

                    # Validate required keys before building
                    required = {"user_question", "tool_query", "simulated_result", "final_answer"}
                    if not required.issubset(traj_raw.keys()):
                        missing = required - traj_raw.keys()
                        logger.warning(
                            "generate_dataset worker %d: LLM response missing keys %s",
                            _idx, missing,
                        )
                        await queue.put(
                            json.dumps({"__skipped__": f"worker_{_idx}_missing_{missing}"}) + "\n"
                        )
                        return

                    record = _build_trajectory(traj_raw)
                    await queue.put(json.dumps(record, ensure_ascii=False) + "\n")

                except Exception as exc:
                    logger.exception("generate_dataset worker %d failed: %s", _idx, exc)
                    await queue.put(json.dumps({"__error__": str(exc)}) + "\n")

                finally:
                    completed += 1
                    if completed >= total:
                        await queue.put(None)  # end-of-stream sentinel

        # Spawn all workers — they are throttled by the semaphore
        tasks = [asyncio.create_task(_worker(i)) for i in range(num_samples)]

        yielded = 0
        while yielded < num_samples:
            item = await queue.get()
            if item is None:
                break
            yield item
            yielded += 1

        # Cancel any lingering tasks (shouldn't happen in normal flow)
        for t in tasks:
            t.cancel()

    return StreamingResponse(
        _pipeline_generator(),
        media_type="application/jsonlines",
        headers={
            "Content-Disposition": f'attachment; filename="{filename}"',
            "X-Content-Type-Options": "nosniff",
            "Cache-Control": "no-store",
            "X-Spaider-Samples": str(num_samples),
            "X-Spaider-Concurrency": str(concurrency),
        },
    )


@router.get("/download/{dataset_id}")
async def download_dataset(dataset_id: str):
    """
    Download a previously generated dataset as a .jsonl file.
    The dataset_id is returned by the POST /synthesize endpoint.
    """
    # Datasets are stored in /tmp/spaider_datasets/ by the synthesizer
    dataset_dir = Path("/tmp/spaider_datasets")
    candidates = list(dataset_dir.glob(f"{dataset_id}*.jsonl"))

    if not candidates:
        raise HTTPException(
            status_code=404,
            detail=f"Dataset '{dataset_id}' not found. It may have expired or the id is incorrect.",
        )

    dataset_path = candidates[0]

    if not dataset_path.exists():
        raise HTTPException(status_code=404, detail=f"Dataset file not found: {dataset_path}")

    return FileResponse(
        path=str(dataset_path),
        media_type="application/jsonlines",
        filename=dataset_path.name,
        headers={"Content-Disposition": f'attachment; filename="{dataset_path.name}"'},
    )
