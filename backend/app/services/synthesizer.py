"""
Model Synthesizer: Generates LLM fine-tuning datasets from the knowledge graph.
Supports three strategies: Factual Q&A, Reasoning Chains, Relation Extraction.
Output formats: OpenAI messages format, Alpaca instruction format.
"""
from __future__ import annotations

import json
import logging
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from pydantic import BaseModel, Field

from app.config import settings
from app.lib.litellm_retry import acompletion_with_retry
from app.models.schemas import Edge, GraphPayload, Node
from app.services.graph_service import GraphService

logger = logging.getLogger(__name__)

_SYNTH_PROMPT_PATH = Path(__file__).parent.parent / "prompts" / "synthesizer_qa.txt"
_OUTPUT_DIR = Path("/tmp/spaider/datasets")

_MIN_CONFIDENCE = 0.0  # placeholder – nodes/edges don't have confidence yet; extend as needed


# ---------------------------------------------------------------------------
# Config / Result models
# ---------------------------------------------------------------------------

class SynthesizeConfig(BaseModel):
    strategies: list[str] = Field(
        default=["factual_qa", "reasoning_chains", "relation_extraction"],
        description="Which synthesis strategies to run.",
    )
    output_format: str = Field(
        default="openai",
        description="'openai' for messages format, 'alpaca' for instruction/context/response.",
    )
    node_types: Optional[list[str]] = Field(
        default=None,
        description="Filter to specific node types; None = all.",
    )
    min_confidence: float = Field(
        default=0.0,
        description="Minimum confidence score for nodes/edges to include.",
    )
    max_examples: int = Field(
        default=500,
        description="Maximum total training examples to generate.",
    )


class SynthesizeResult(BaseModel):
    agent_id: str
    output_path: str
    total_examples: int
    strategy_counts: dict[str, int]
    duplicate_count: int


# ---------------------------------------------------------------------------
# ModelSynthesizer
# ---------------------------------------------------------------------------

class ModelSynthesizer:
    """
    Generates structured training data from a SpAIder knowledge graph.

    Strategies:
      1. factual_qa         – One-hop Q&A from high-degree nodes
      2. reasoning_chains   – Multi-hop Q&A from paths of length 2-4
      3. relation_extraction – Given context, extract subject/relation/object
    """

    def __init__(self, graph_service: GraphService) -> None:
        self._graph = graph_service
        self._qa_prompt = self._load_prompt()

    # ------------------------------------------------------------------
    # Setup
    # ------------------------------------------------------------------

    def _load_prompt(self) -> str:
        try:
            return _SYNTH_PROMPT_PATH.read_text(encoding="utf-8")
        except FileNotFoundError:
            logger.warning("synthesizer_qa prompt not found; using default.")
            return (
                "You are a training data generator for knowledge graphs. "
                "Given graph facts, generate high-quality question-answer pairs. "
                "Return a JSON list of objects with keys: instruction, context, response."
            )

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def synthesize(
        self,
        agent_id: str,
        config: SynthesizeConfig,
    ) -> SynthesizeResult:
        """
        Run all requested synthesis strategies and save JSONL output.

        Args:
            agent_id: Agent whose graph to use.
            config: Synthesis configuration.

        Returns:
            SynthesizeResult with stats and output path.
        """
        graph = await self._graph.get_full_graph(agent_id, limit=5000)

        # Apply node type filter
        if config.node_types:
            types_lower = {t.lower() for t in config.node_types}
            graph = GraphPayload(
                nodes=[n for n in graph.nodes if n.type.lower() in types_lower],
                edges=graph.edges,
            )

        if not graph.nodes:
            logger.warning("synthesize: no nodes found for agent %s", agent_id)

        node_map = {n.id: n for n in graph.nodes}
        examples: list[dict] = []
        strategy_counts: dict[str, int] = {}

        for strategy in config.strategies:
            if strategy == "factual_qa":
                new_examples = await self._factual_qa(graph, node_map, config)
            elif strategy == "reasoning_chains":
                new_examples = await self._reasoning_chains(graph, node_map, config)
            elif strategy == "relation_extraction":
                new_examples = await self._relation_extraction(graph, node_map, config)
            else:
                logger.warning("Unknown synthesis strategy: %s", strategy)
                new_examples = []

            strategy_counts[strategy] = len(new_examples)
            examples.extend(new_examples)

            if len(examples) >= config.max_examples:
                examples = examples[: config.max_examples]
                break

        # Deduplicate
        seen: set[str] = set()
        deduped: list[dict] = []
        for ex in examples:
            key = ex.get("instruction", "") + ex.get("response", "")
            if key not in seen:
                seen.add(key)
                deduped.append(ex)

        duplicate_count = len(examples) - len(deduped)

        # Format and save
        output_path = self._save_jsonl(agent_id, deduped, config.output_format)

        logger.info(
            "synthesize | agent=%s total=%d dupes=%d path=%s",
            agent_id, len(deduped), duplicate_count, output_path,
        )

        return SynthesizeResult(
            agent_id=agent_id,
            output_path=output_path,
            total_examples=len(deduped),
            strategy_counts=strategy_counts,
            duplicate_count=duplicate_count,
        )

    # ------------------------------------------------------------------
    # Strategy 1: Factual Q&A
    # ------------------------------------------------------------------

    async def _factual_qa(
        self,
        graph: GraphPayload,
        node_map: dict[str, Node],
        config: SynthesizeConfig,
    ) -> list[dict]:
        """Generate Q&A from nodes with 2+ edges."""
        # Find nodes with >= 2 edges
        edge_count: dict[str, int] = {}
        for edge in graph.edges:
            edge_count[edge.source_id] = edge_count.get(edge.source_id, 0) + 1
            edge_count[edge.target_id] = edge_count.get(edge.target_id, 0) + 1

        high_degree = [
            node_map[nid]
            for nid, cnt in edge_count.items()
            if cnt >= 2 and nid in node_map
        ][:50]  # limit batch size

        if not high_degree:
            return []

        facts = self._nodes_to_facts(high_degree, graph.edges, node_map)
        return await self._llm_generate_qa(facts, "factual_qa", config.max_examples // 3)

    # ------------------------------------------------------------------
    # Strategy 2: Reasoning Chains
    # ------------------------------------------------------------------

    async def _reasoning_chains(
        self,
        graph: GraphPayload,
        node_map: dict[str, Node],
        config: SynthesizeConfig,
    ) -> list[dict]:
        """Find paths of length 2-4 and generate multi-hop Q&A."""
        paths = self._find_paths(graph, node_map, min_len=2, max_len=4, max_paths=30)
        if not paths:
            return []

        path_strs: list[str] = []
        for path in paths:
            parts = []
            for i, node in enumerate(path):
                parts.append(node.label)
                if i < len(path) - 1:
                    # Find edge between path[i] and path[i+1]
                    rel = self._find_relation(graph.edges, node.id, path[i + 1].id)
                    parts.append(f"--[{rel}]-->")
            path_strs.append(" ".join(parts))

        facts = "\n".join(path_strs)
        return await self._llm_generate_qa(facts, "reasoning_chains", config.max_examples // 3)

    # ------------------------------------------------------------------
    # Strategy 3: Relation Extraction
    # ------------------------------------------------------------------

    async def _relation_extraction(
        self,
        graph: GraphPayload,
        node_map: dict[str, Node],
        config: SynthesizeConfig,
    ) -> list[dict]:
        """Generate relation extraction training examples (SPO triples)."""
        if not graph.edges:
            return []

        examples: list[dict] = []
        for edge in graph.edges[:100]:
            src = node_map.get(edge.source_id)
            tgt = node_map.get(edge.target_id)
            if not src or not tgt:
                continue
            context = (
                f"{src.label} ({src.type}) is related to {tgt.label} ({tgt.type}) "
                f"via '{edge.relation}'."
            )
            instruction = (
                "Extract the relationship between entities mentioned in the following text."
            )
            response = json.dumps({
                "subject": src.label,
                "relation": edge.relation,
                "object": tgt.label,
            })
            examples.append(
                {"instruction": instruction, "context": context, "response": response}
            )

        return examples[: config.max_examples // 3]

    # ------------------------------------------------------------------
    # LLM helper
    # ------------------------------------------------------------------

    async def _llm_generate_qa(
        self, facts: str, strategy: str, max_count: int
    ) -> list[dict]:
        """Ask the LLM to generate Q&A pairs from graph facts."""
        try:
            call_kwargs: dict = dict(
                model=settings.litellm_model,
                messages=[
                    {"role": "system", "content": self._qa_prompt},
                    {
                        "role": "user",
                        "content": (
                            f"Strategy: {strategy}\n"
                            f"Generate up to {max_count} training examples from these facts:\n\n"
                            f"{facts}\n\n"
                            "Return a JSON array of objects with keys: instruction, context, response."
                        ),
                    },
                ],
                temperature=0.7,
                max_tokens=settings.llm_max_tokens,
            )
            if settings.llm_base_url:
                call_kwargs["api_base"] = settings.llm_base_url
            if settings.llm_api_key:
                call_kwargs["api_key"] = settings.llm_api_key

            response = await acompletion_with_retry(**call_kwargs)
            raw = response.choices[0].message.content or "[]"
            raw = raw.strip()
            if raw.startswith("```"):
                nl = raw.find("\n")
                raw = raw[nl + 1:] if nl != -1 else raw[3:]
                raw = raw.rstrip("`").strip()
            data = json.loads(raw)
            if isinstance(data, list):
                return data[:max_count]
            return []
        except Exception as exc:
            logger.error("LLM Q&A generation failed (%s): %s", strategy, exc)
            return []

    # ------------------------------------------------------------------
    # Graph helpers
    # ------------------------------------------------------------------

    @staticmethod
    def _nodes_to_facts(
        nodes: list[Node],
        edges: list[Edge],
        node_map: dict[str, Node],
    ) -> str:
        lines: list[str] = []
        edge_index: dict[str, list[Edge]] = {}
        for edge in edges:
            edge_index.setdefault(edge.source_id, []).append(edge)

        for node in nodes:
            outgoing = edge_index.get(node.id, [])
            for edge in outgoing:
                tgt = node_map.get(edge.target_id)
                if tgt:
                    lines.append(
                        f"{node.label} ({node.type}) --[{edge.relation}]--> {tgt.label} ({tgt.type})"
                    )
        return "\n".join(lines)

    @staticmethod
    def _find_paths(
        graph: GraphPayload,
        node_map: dict[str, Node],
        min_len: int,
        max_len: int,
        max_paths: int,
    ) -> list[list[Node]]:
        """Simple BFS path finder."""
        adj: dict[str, list[str]] = {}
        for edge in graph.edges:
            adj.setdefault(edge.source_id, []).append(edge.target_id)

        paths: list[list[Node]] = []
        queue: list[list[str]] = [[nid] for nid in node_map]

        while queue and len(paths) < max_paths:
            path = queue.pop(0)
            if len(path) > max_len + 1:
                continue
            if min_len <= len(path) - 1 <= max_len:
                node_path = [node_map[nid] for nid in path if nid in node_map]
                if len(node_path) == len(path):
                    paths.append(node_path)
            if len(path) <= max_len:
                last = path[-1]
                for neighbor in adj.get(last, []):
                    if neighbor not in path:  # avoid cycles
                        queue.append(path + [neighbor])

        return paths

    @staticmethod
    def _find_relation(edges: list[Edge], src_id: str, tgt_id: str) -> str:
        for edge in edges:
            if edge.source_id == src_id and edge.target_id == tgt_id:
                return edge.relation
        return "RELATED_TO"

    # ------------------------------------------------------------------
    # Output
    # ------------------------------------------------------------------

    def _save_jsonl(
        self,
        agent_id: str,
        examples: list[dict],
        output_format: str,
    ) -> str:
        timestamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        out_dir = _OUTPUT_DIR / agent_id
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{timestamp}.jsonl"

        from app.utils.jsonl_formatter import to_alpaca_format, to_openai_format

        lines: list[str] = []
        for ex in examples:
            instruction = ex.get("instruction", "")
            context = ex.get("context", "")
            response = ex.get("response", "")

            if output_format == "openai":
                lines.append(to_openai_format(instruction, context, response))
            else:
                lines.append(to_alpaca_format(instruction, context, response))

        out_path.write_text("\n".join(lines), encoding="utf-8")
        return str(out_path)
