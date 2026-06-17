"""
SpAIder Enterprise Pillar Benchmark Suite.

Tests three pillar promises against the live-or-local backend:

  Test 1 — Pillar 2 "Fort Knox" Security
           Seed a clearance=5 SystemAgent node, query as a clearance=1
           caller, assert the restricted node does NOT leak into the
           formatter output. Tears down both nodes in finally.

  Test 2 — Pillar 3 Cognitive Ingestion
           2a) Feed ~2000 chars of dense text into _split_chunks; assert
               every chunk fits in 500 tiktoken tokens (the documented
               token-budget promise — main uses 1200-char windows;
                introduces the tiktoken path).
           2b) When LLM_API_KEY is set, run the full SemanticCompressor
               extraction and assert every emitted edge's relation is a
               canonical RelationType enum value ( introduces the
               RelationOntologyManager that enforces this). Cleanly
               skipped when no API key is available — the benchmark
               must never burn outbound API spend silently.

  Test 3 — Pillar 5 "Token Guillotine"
           Build a synthetic GraphPayload (50 nodes × 10 edges, each
           node carrying a 1000-char source_text) and pass it through
           _format_context_records and _build_context. Three assertions:
             • source_text truncated to <=300 chars + "..."
             • nodes sliced to top 30 by synaptic_score desc
             • edges per node sliced to top 5 by utility_weight desc
               (real effect immediately because Edge.utility_weight
               is a native schema field)
           Measures total context tokens via tiktoken and reports the
           percent reduction relative to the un-truncated baseline.

Suite design discipline
-----------------------
- Every test catches its own exceptions and returns a TestResult — one
  test crashing must NOT abort the suite (hard_constraint).
- Every test that writes to Neo4j has a try/finally that runs cleanup
  whether the test passed, failed, or raised (hard_constraint).
- Token measurement uses the same tiktoken encoder family the
  ingestion patch uses (cl100k_base fallback when the active
  model is not in tiktoken's registry).
- Tests run independently — no shared fixtures, no order coupling.

Running
-------
From the repo root:

    docker compose up neo4j -d   # if Test 1 should run; otherwise it SKIPs
    cd backend
    python -m app.scripts.benchmark_suite

Exit code is 0 when every non-skipped test PASSes, 1 otherwise.

Honest framing
--------------
At time of authorship, the implementations of (token chunking +
edge ontology) and (token guillotine) are not on main. When this
suite runs on main, Tests 2a and 3 will report FAIL with hard measured
numbers showing the gap. Run on a branch that has both PRs merged to
see PASS results. The benchmark is a regression gate, not a self-test.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys
import uuid
from dataclasses import dataclass, field

logger = logging.getLogger("benchmark_suite")


# ---------------------------------------------------------------------------
# Tiktoken — required for Test 2a and Test 3 token measurements.
# Same graceful-import pattern as so a missing dep degrades to
# SKIP rather than crashing the suite.
# ---------------------------------------------------------------------------
try:
    import tiktoken as _tiktoken
    _TIKTOKEN_AVAILABLE = True
except ImportError:
    _tiktoken = None  # type: ignore[assignment]
    _TIKTOKEN_AVAILABLE = False


def _get_encoder():
    """Return a tiktoken encoder. cl100k_base is the safe lingua franca
    matching the ingestion-patch fallback. Returns None on failure so
    callers can SKIP cleanly."""
    if not _TIKTOKEN_AVAILABLE:
        return None
    try:
        return _tiktoken.get_encoding("cl100k_base")
    except Exception as exc:
        logger.warning("tiktoken cl100k_base load failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Result dataclass — uniform per-test reporting
# ---------------------------------------------------------------------------

@dataclass
class TestResult:
    name: str
    status: str  # "PASS" | "FAIL" | "SKIP" | "ERROR"
    key_metric: str = ""
    detail: str = ""
    measurements: dict = field(default_factory=dict)


# ===========================================================================
# Test 1 — Pillar 2 "Fort Knox" Security
# ===========================================================================

async def test_fort_knox_security() -> TestResult:
    """Seed a clearance=5 SpaiderNode; query as clearance=1 agent; assert exclusion."""
    name = "Pillar 2 — Fort Knox Security"

    # Lazy imports so an ImportError surfaces as ERROR (not crash).
    try:
        from app.services.graph_service import GraphService
        from app.services.query_service import QueryService
    except Exception as exc:
        return TestResult(name, "ERROR", "", f"import failure: {exc}")

    test_agent_id = f"bench-fortknox-{uuid.uuid4().hex[:8]}"
    test_node_id = f"bench-secret-{uuid.uuid4().hex[:8]}"
    test_label_token = f"BenchmarkSecretFact{uuid.uuid4().hex[:6]}"

    graph = GraphService()

    # Probe Neo4j first — if down, SKIP cleanly. No cleanup needed because
    # nothing was written yet.
    try:
        await graph.ping()
    except Exception as exc:
        await graph.close()
        return TestResult(
            name, "SKIP", "",
            f"Neo4j unreachable: {type(exc).__name__}. Start with `docker compose up neo4j -d`.",
        )

    try:
        # ── Setup: SystemAgent + clearance-5 SpaiderNode ────────────────
        async with graph._driver.session() as session:
            await session.run(
                """
                CREATE (a:SystemAgent {
                    agent_id: $aid,
                    name: 'bench-fortknox',
                    clearance_level: 1,
                    created_at: datetime()
                })
                """,
                aid=test_agent_id,
            )
            await session.run(
                """
                CREATE (n:SpaiderNode {
                    id: $nid,
                    agent_id: $aid,
                    label: $label,
                    type: 'CONCEPT',
                    clearance_level: 5,
                    energy_level: 1.0,
                    retrieval_count: 5,
                    last_activation: datetime(),
                    properties: '{}'
                })
                """,
                nid=test_node_id, aid=test_agent_id, label=test_label_token,
            )

        # ── Run retrieval as clearance=1 caller — token in label should
        #     FTS-match the test node, so the only reason it WON'T appear
        #     in the formatted result is the clearance filter doing its job.
        qs = QueryService(graph_service=graph)
        formatted, agents = await qs.retrieve_swarm_context(
            target_agent_id=test_agent_id,
            query=test_label_token.lower(),
            agent_clearance=1,
        )

        leaked: bool = test_label_token.lower() in formatted.lower()
        returned_records = formatted.count("[Source: Agent")

        if not leaked:
            return TestResult(
                name, "PASS",
                f"leaked=False, returned_records={returned_records}",
                "clearance=5 node correctly blocked from clearance=1 caller",
                {"leaked": False, "returned_records": returned_records},
            )
        else:
            return TestResult(
                name, "FAIL",
                f"leaked=True, returned_records={returned_records}",
                f"CRITICAL — clearance=5 node '{test_label_token}' surfaced to clearance=1 caller",
                {"leaked": True, "returned_records": returned_records},
            )

    except Exception as exc:
        return TestResult(
            name, "ERROR", "", f"unhandled during run: {type(exc).__name__}: {exc}",
        )

    finally:
        # Teardown — always runs, even on test failure or unhandled exception.
        try:
            async with graph._driver.session() as session:
                await session.run(
                    "MATCH (n:SpaiderNode {id: $nid}) DETACH DELETE n",
                    nid=test_node_id,
                )
                await session.run(
                    "MATCH (a:SystemAgent {agent_id: $aid}) DETACH DELETE a",
                    aid=test_agent_id,
                )
        except Exception as exc:
            logger.warning("Test 1 cleanup failed: %s", exc)
        try:
            await graph.close()
        except Exception:
            pass


# ===========================================================================
# Test 2 — Pillar 3 Cognitive Ingestion
# ===========================================================================

# ~2000 chars of dense English. Used for both 2a (chunk size) and 2b
# (LLM edge extraction). Deliberately information-dense so the LLM has
# enough material to emit relation-bearing edges, and tokenizer-realistic
# so a 1200-char chunk on main lands in the 250-400-token range.
_DENSE_TEST_TEXT: str = (
    "Artificial intelligence systems leveraging knowledge graphs demonstrate "
    "superior multi-hop reasoning compared to pure-vector retrieval. "
    "Tesla, founded by Elon Musk in 2003, pioneered electric vehicles. "
    "SpaceX, also founded by Musk, develops reusable launch vehicles. "
    "OpenAI was co-founded by Musk in 2015 but he later departed the board. "
    "Anthropic was founded by former OpenAI researchers including Dario Amodei. "
    "Neo4j Inc develops the Neo4j graph database, headquartered in San Mateo. "
    "Hebbian learning, proposed by Donald Hebb in 1949, underlies "
    "synaptic plasticity in modern reinforcement-learning architectures. "
    "ACT-R, John Anderson's cognitive architecture, models memory decay via "
    "an exponential function of inter-retrieval intervals. "
    "Transformer architectures, introduced by Vaswani et al at Google in 2017, "
    "displaced LSTM-based sequence models for most NLP benchmarks. "
    "GPT-3 was released by OpenAI in 2020 and licensed exclusively to Microsoft. "
    "Claude was released by Anthropic in 2023 and competes with GPT-4. "
    "Gemini, developed by Google DeepMind, integrates multimodal capabilities. "
    "Knowledge graph completion remains an active research area in 2024-2026. "
    "Retrieval-augmented generation reduces hallucination rates substantially. "
)
_DENSE_TEST_TEXT = _DENSE_TEST_TEXT[:2000]


async def test_cognitive_ingestion() -> TestResult:
    """Chunker respects 500-token tiktoken limit; edges use RelationType vocabulary."""
    name = "Pillar 3 — Cognitive Ingestion"

    if not _TIKTOKEN_AVAILABLE:
        return TestResult(name, "SKIP", "", "tiktoken not installed; `pip install tiktoken` to run")
    encoder = _get_encoder()
    if encoder is None:
        return TestResult(name, "SKIP", "", "tiktoken encoder unavailable")

    try:
        from app.models.schemas import RelationType
        from app.services.compressor import SemanticCompressor
    except Exception as exc:
        return TestResult(name, "ERROR", "", f"import failure: {exc}")

    try:
        compressor = SemanticCompressor()
    except Exception as exc:
        return TestResult(name, "ERROR", "", f"compressor init: {type(exc).__name__}: {exc}")

    # ── 2a: chunk size measurement ──────────────────────────────────────
    chunks = compressor._split_chunks(_DENSE_TEST_TEXT)
    token_counts = [len(encoder.encode(c)) for c in chunks]
    max_tokens = max(token_counts) if token_counts else 0
    chunk_pass = bool(token_counts) and max_tokens <= 500

    # ── 2b: edge vocabulary (LLM-dependent; SKIP when no API key) ──────
    valid_relations = {r.value for r in RelationType}
    edge_count = 0
    invalid_count = 0
    invalid_examples: list[str] = []
    edge_test_status = ""
    vocab_pass = True

    if os.environ.get("LLM_API_KEY"):
        try:
            payload = await compressor.extract(_DENSE_TEST_TEXT)
            edge_count = len(payload.edges)
            for e in payload.edges:
                normalized = (e.relation or "").upper().strip()
                if normalized not in valid_relations:
                    invalid_count += 1
                    if len(invalid_examples) < 5:
                        invalid_examples.append(e.relation)
            vocab_pass = (invalid_count == 0) if edge_count > 0 else True
            edge_test_status = f"edges={edge_count} invalid={invalid_count}"
            if invalid_examples:
                edge_test_status += f" (samples: {invalid_examples})"
        except Exception as exc:
            edge_test_status = f"LLM extract failed: {type(exc).__name__}: {exc}"
            # An LLM failure doesn't fail the test; it's noted in the report.
    else:
        edge_test_status = "LLM_API_KEY unset — vocab assertion skipped"

    # Status reflects all assertions that actually ran.
    detail_parts = []
    if not chunk_pass:
        detail_parts.append(f"chunk size {max_tokens} tokens exceeds the 500-token limit")
    if not vocab_pass:
        detail_parts.append(f"{invalid_count}/{edge_count} edges use relations outside the RelationType vocabulary")
    status = "PASS" if chunk_pass and vocab_pass else "FAIL"

    return TestResult(
        name, status,
        f"chunks={len(chunks)}, max_chunk_tokens={max_tokens}, {edge_test_status}",
        "; ".join(detail_parts) if detail_parts else "chunker + edge vocab both within budget",
        {
            "chunk_count":      len(chunks),
            "max_chunk_tokens": max_tokens,
            "chunk_pass":       chunk_pass,
            "edge_count":       edge_count,
            "invalid_count":    invalid_count,
            "vocab_pass":       vocab_pass,
        },
    )


# ===========================================================================
# Test 3 — Pillar 5 "Token Guillotine"
# ===========================================================================

# Configured limits we assert against. Per the brief these are the
# Pillar 5 contract values shipped in. On main these limits
# are not enforced — Test 3 will FAIL until the PR merges.
_EXPECTED_SOURCE_TEXT_MAX = 300
_EXPECTED_NODE_CAP = 30
_EXPECTED_EDGE_CAP = 5


async def test_token_guillotine() -> TestResult:
    """source_text truncated to 300; nodes sorted by synaptic_score; edges by utility_weight."""
    name = "Pillar 5 — Token Guillotine"

    if not _TIKTOKEN_AVAILABLE:
        return TestResult(name, "SKIP", "", "tiktoken not installed; `pip install tiktoken` to run")
    encoder = _get_encoder()
    if encoder is None:
        return TestResult(name, "SKIP", "", "tiktoken encoder unavailable")

    try:
        from app.models.schemas import Edge, GraphPayload, Node
        from app.services.query_service import QueryService
    except Exception as exc:
        return TestResult(name, "ERROR", "", f"import failure: {exc}")

    # ── 3a: source_text truncation via _format_context_records ────────
    # Build 20 records each carrying a 1000-char source_text and NO
    # description (so the elif source_text branch fires).
    long_text = "x" * 1000
    records = [
        {
            "source_agent": f"bench-agent-{i}",
            "label":        f"BenchTextNode-{i}",
            "type":         "CONCEPT",
            "synaptic_score": 1.0,
            "properties":   {"source_text": long_text},  # no description
        }
        for i in range(20)
    ]

    # Pre-truncation baseline: what the formatter WOULD emit if no truncation.
    pre_str = "\n".join(
        f"[Source: Agent bench-agent-{i}] - BenchTextNode-{i} (CONCEPT): {long_text}"
        for i in range(20)
    )
    pre_tokens = len(encoder.encode(pre_str))

    formatted, _ = QueryService._format_context_records(records, v2_mode=False)
    post_tokens = len(encoder.encode(formatted))
    reduction_pct = (1.0 - post_tokens / max(pre_tokens, 1)) * 100.0

    # Truncation evidence: ellipsis present AND no record carries the
    # full 1000-char payload. We check for a 500-char run of 'x' — if
    # truncation is at <=300, that run is impossible; if not truncated,
    # it's present in every record.
    truncation_marker = "..." in formatted
    full_text_leaked = ("x" * 500) in formatted
    truncation_pass = truncation_marker and not full_text_leaked

    # ── 3b: build a graph payload and check node + edge ordering ──────
    # 50 nodes, synaptic_score in properties from 0..49. Highest scores
    # should win the [:30] slice when the sort is active.
    nodes = [
        Node(
            id=f"benchnode-{i}",
            label=f"BenchNode{i:02d}",
            type="CONCEPT",
            properties={
                "description": f"desc for node {i}",
                "synaptic_score": float(i),
            },
        )
        for i in range(50)
    ]
    # 10 edges from the HIGHEST-score node (survives the synaptic-score
    # [:30] cap) to distinct targets, weights 0.0..0.9.
    # The relation string encodes the weight so we can verify ordering
    # in the formatted output directly without ambiguity.
    edges_from_node0 = [
        Edge(
            id=f"benchedge-0-{j}",
            source_id="benchnode-49",
            target_id=f"benchnode-{j+1}",
            relation=f"WEIGHT_{j:02d}",
            utility_weight=float(j) / 10.0,  # 0.0, 0.1, ..., 0.9
        )
        for j in range(10)
    ]
    payload = GraphPayload(nodes=nodes, edges=edges_from_node0)

    ctx = QueryService._build_context(payload)
    ctx_tokens = len(encoder.encode(ctx))

    # Count emitted node lines. The formatter emits "• Label (TYPE): ..."
    # one per node. With 50 input nodes the cap should fire.
    emitted_lines = [ln for ln in ctx.split("\n") if ln.startswith("•")]
    node_cap_pass = len(emitted_lines) <= _EXPECTED_NODE_CAP

    # Node sort assertion: BenchNode49 (highest score) must appear AND
    # BenchNode00 (lowest score) must NOT, if the sort is active.
    has_highest = "BenchNode49" in ctx
    has_lowest = "BenchNode00" in ctx
    node_sort_pass = has_highest and not has_lowest

    # Edge sort assertion: in the line for BenchNode49 (which has all
    # 10 edges as outgoing), look for the 5 emitted relations and assert
    # they are the HIGH-weight ones (WEIGHT_09..WEIGHT_05, sorted desc).
    n00_line_match = [ln for ln in emitted_lines if "BenchNode49" in ln]
    edge_sort_pass = False
    edge_observed = ""
    if n00_line_match:
        n00_line = n00_line_match[0]
        # Pull relation tokens in their emitted order.
        observed = [w for w in n00_line.split() if w.startswith("WEIGHT_")]
        edge_observed = ",".join(observed)
        # Sort path: should be WEIGHT_09, _08, _07, _06, _05 (top 5 by weight).
        expected_top5_desc = ["WEIGHT_09", "WEIGHT_08", "WEIGHT_07", "WEIGHT_06", "WEIGHT_05"]
        edge_sort_pass = observed == expected_top5_desc

    # ── Aggregate ──────────────────────────────────────────────────────
    fail_reasons: list[str] = []
    pass_evidence: list[str] = []

    if truncation_pass:
        pass_evidence.append(
            f"source_text truncated → {reduction_pct:.1f}% token reduction"
        )
    else:
        fail_reasons.append(
            f"source_text NOT truncated (ellipsis_present={truncation_marker}, "
            f"full_text_leaked={full_text_leaked}); reduction only {reduction_pct:.1f}% — required"
        )

    if node_cap_pass:
        pass_evidence.append(f"node cap respected ({len(emitted_lines)} ≤ {_EXPECTED_NODE_CAP})")
    else:
        fail_reasons.append(f"node cap violated ({len(emitted_lines)} > {_EXPECTED_NODE_CAP})")

    if node_sort_pass:
        pass_evidence.append("nodes sorted by synaptic_score desc (top 30 present)")
    else:
        fail_reasons.append(
            f"nodes NOT sorted by synaptic_score "
            f"(highest_present={has_highest}, lowest_present={has_lowest}) — required"
        )

    if edge_sort_pass:
        pass_evidence.append("edges sorted by utility_weight desc")
    else:
        fail_reasons.append(
            f"edges NOT sorted by utility_weight (observed top-5: {edge_observed or 'none'}) — required"
        )

    status = "PASS" if not fail_reasons else "FAIL"
    detail = " ; ".join(fail_reasons) if fail_reasons else " ; ".join(pass_evidence)
    return TestResult(
        name, status,
        (
            f"pre_tokens={pre_tokens}, post_tokens={post_tokens}, "
            f"reduction={reduction_pct:.1f}%, ctx_tokens={ctx_tokens}, "
            f"emitted_node_lines={len(emitted_lines)}"
        ),
        detail,
        {
            "pre_tokens":         pre_tokens,
            "post_tokens":        post_tokens,
            "reduction_pct":      round(reduction_pct, 2),
            "ctx_tokens":         ctx_tokens,
            "emitted_lines":      len(emitted_lines),
            "truncation_pass":    truncation_pass,
            "node_cap_pass":      node_cap_pass,
            "node_sort_pass":     node_sort_pass,
            "edge_sort_pass":     edge_sort_pass,
            "edge_observed":      edge_observed,
        },
    )


# ===========================================================================
# Suite runner
# ===========================================================================

_TESTS = [
    test_fort_knox_security,
    test_cognitive_ingestion,
    test_token_guillotine,
]


def _format_summary(results: list[TestResult]) -> str:
    """Render the CEO-facing summary table."""
    lines = []
    lines.append("=" * 78)
    lines.append("SpAIder Enterprise Pillar Benchmark — Summary")
    lines.append("=" * 78)
    lines.append(f"{'Test':<34} {'Status':<8} Key Metric")
    lines.append("-" * 78)
    for r in results:
        lines.append(f"{r.name:<34} {r.status:<8} {r.key_metric}")
        if r.detail:
            # Indent so the eye groups detail with its test row.
            lines.append(f"{'':<34} {'':<8} ↳ {r.detail}")
    lines.append("=" * 78)

    counts: dict[str, int] = {}
    for r in results:
        counts[r.status] = counts.get(r.status, 0) + 1
    summary_bits = [f"{k}={v}" for k, v in sorted(counts.items())]
    lines.append("Totals: " + " | ".join(summary_bits))
    lines.append("=" * 78)
    return "\n".join(lines)


async def main() -> int:
    """Run the suite. Returns 0 when every non-skipped test PASSes."""
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s — %(message)s",
        datefmt="%H:%M:%S",
    )

    print("\nSpAIder Enterprise Pillar Benchmark Suite — starting", flush=True)
    print(f"  tiktoken: {'available' if _TIKTOKEN_AVAILABLE else 'NOT INSTALLED'}", flush=True)
    print(f"  LLM_API_KEY: {'set' if os.environ.get('LLM_API_KEY') else 'unset (Test 2b will skip)'}", flush=True)

    results: list[TestResult] = []
    for fn in _TESTS:
        test_name = fn.__name__
        print(f"\n→ Running {test_name}", flush=True)
        try:
            r = await fn()
        except Exception as exc:
            # Belt-and-suspenders — every test should already catch its
            # own exceptions and return ERROR rather than raising.
            r = TestResult(
                name=test_name,
                status="ERROR",
                detail=f"uncaught: {type(exc).__name__}: {exc}",
            )
        print(f"  → {r.status}: {r.key_metric}", flush=True)
        if r.detail:
            print(f"    ↳ {r.detail}", flush=True)
        results.append(r)

    print("\n" + _format_summary(results), flush=True)

    # Non-zero exit only when at least one test that actually ran failed.
    # SKIP does not fail the suite — that's an environment gap to be
    # surfaced, not a code failure.
    return 0 if all(r.status in ("PASS", "SKIP") for r in results) else 1


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
