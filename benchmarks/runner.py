"""
SpAIder benchmark runner.

Runs a YAML-defined task suite against any LLM provider supported by
LiteLLM (OpenAI, Anthropic, Ollama, Azure, Groq, …) in two modes:

  --mode vanilla    Plain prompt, no tools. Baseline.
  --mode with-spaider   Same prompt, but the model can call SpAIder MCP tools
                    (`spaider.query`, `spaider.list_recent`,
                    `spaider.ingest_fact`). The runner relays tool calls
                    through `mcp.client.sse` to the configured server.

Each (task, mode) execution emits one JSONL row to ``benchmarks/runs/``.
The Streamlit dashboard reads the same JSONL.

Provider config mirrors SpAIder's own LiteLLM contract — the same env
vars from `.env.example` work here unchanged:

  LLM_PROVIDER   openai | anthropic | ollama | azure | groq | …
  LLM_MODEL      provider-specific model id (gpt-4o-mini, llama3.2:3b, …)
  LLM_API_KEY    provider key (any non-empty string for Ollama)
  LLM_BASE_URL   override endpoint (Ollama, Azure, custom)

CLI ``--provider`` and ``--model`` override the env vars. With-MCP mode
additionally needs ``SPAIDER_API_KEY`` (the dev-{user} agent's key) and
optionally ``SPAIDER_MCP_URL`` (default: host-side standalone on :8001).

Usage
-----
    pip install -e benchmarks[dashboard]    # one-time

    # Free local sweep with Ollama (small model, tool-capable):
    export LLM_PROVIDER=ollama LLM_MODEL=llama3.2:3b
    export LLM_BASE_URL=http://localhost:11434
    python -m benchmarks.runner --tasks benchmarks/tasks --mode vanilla

    # Cloud sweep:
    export LLM_PROVIDER=openai LLM_MODEL=gpt-4o-mini LLM_API_KEY=sk-...
    python -m benchmarks.runner --tasks benchmarks/tasks --mode with-spaider

    # Override on the CLI without touching env:
    python -m benchmarks.runner --tasks benchmarks/tasks --mode vanilla \\
        --provider anthropic --model claude-haiku-4-5
"""
from __future__ import annotations

import argparse
import asyncio
import json
import logging
import os
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING, Any, Optional

if TYPE_CHECKING:  # avoids importing competitor adapters (and their deps) at runtime
    from benchmarks.adapters.base import MemorySystemAdapter

import yaml

logger = logging.getLogger("benchmarks.runner")


# ---------------------------------------------------------------------------
# Task definition
# ---------------------------------------------------------------------------


@dataclass
class Task:
    """A single benchmark task loaded from YAML.

    Oracle shapes (signalled by ``oracle_kind``):

    * ``substring`` — v1 task suite. ``expected_substring`` and
      ``expected_all`` are checked against the final text. Cheap,
      deterministic, brittle against paraphrase.
    * ``llm_judge`` — v2 (Compounding Brain) task suite. A separate LLM
      call grades the response against ``oracle_rubric``. Forgiving on
      paraphrase, costs one extra small completion per task.
    * ``f1`` / ``exact_match`` / ``geval`` — DeepEval-style metrics
       for industry-standard QA scoring against a single
      ``expected_output`` ground-truth answer. F1 and exact_match are
      pure-Python (HotpotQA reference impls); geval reuses the LLM-judge
      path with a normalised correctness rubric.
    * ``composite`` — runs F1, exact_match, AND geval on the same task.
      The natural fit for HotpotQA: each task produces all four
      DeepEval-comparable numbers in one row.
    """
    id: str
    title: str
    prompt: str
    # Maximum tokens to spend on this task. Bound runaway loops.
    max_tokens: int = 1024
    # Free-form tag used by the dashboard to group results. Defaults to
    # the task id's filename-stem when not supplied.
    category: str = "default"
    # Oracle dispatch.
    oracle_kind: str = "substring"
    expected_substring: Optional[str] = None
    expected_all: list[str] = field(default_factory=list)
    oracle_rubric: Optional[str] = None
    # DeepEval-style ground-truth answer (used by f1/exact_match/geval/composite).
    expected_output: Optional[str] = None
    # Whether this task is meaningless without SpAIder retrieval — i.e. the
    # answer is genuinely unobtainable without consulting the graph.
    # Documentation hint, not enforced by the runner.
    requires_mcp: bool = False
    # Free-form metadata block from the task YAML. HotpotQA tasks use it to
    # carry ``supporting_titles`` (the gold paragraph titles), which the
    # ``retrieval_hit`` metric checks for in the tool response text.
    properties: dict[str, Any] = field(default_factory=dict)
    # Per-task system-prompt override. Without a system prompt
    # gpt-4o-mini answers HotpotQA bridge questions from training-set
    # knowledge instead of calling spaider.query. The runner ships a
    # ``with-spaider`` default (see ``_DEFAULT_WITH_SPAIDER_SYSTEM_PROMPT``);
    # tasks can override per-YAML for special cases. Empty string disables
    # the default. Only consumed in --mode with-spaider; ignored in vanilla.
    system_prompt: Optional[str] = None
    # Format hint appended *after* the tool result, in the synthesis turn.
    # Lets us tell the model "answer in as few words as possible" without
    # suppressing the initial tool call.
    format_hint: Optional[str] = None

    @classmethod
    def from_yaml(cls, path: Path) -> "Task":
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        oracle_block = data.get("oracle") or {}
        oracle_kind = (
            oracle_block.get("kind")
            or ("llm_judge" if oracle_block.get("rubric") else "substring")
        )
        oracle_rubric = oracle_block.get("rubric")
        return cls(
            id=data["id"],
            title=data["title"],
            prompt=data["prompt"],
            max_tokens=int(data.get("max_tokens", 1024)),
            category=data.get("category", "default"),
            oracle_kind=oracle_kind,
            expected_substring=data.get("expected_substring"),
            expected_all=data.get("expected_all", []) or [],
            oracle_rubric=oracle_rubric,
            expected_output=data.get("expected_output"),
            requires_mcp=bool(data.get("requires_mcp", False)),
            properties=data.get("properties") or {},
            system_prompt=data.get("system_prompt"),
            format_hint=data.get("format_hint"),
        )


def load_tasks(target: Path) -> list[Task]:
    """Accept either a single YAML file or a directory of them."""
    if target.is_file():
        return [Task.from_yaml(target)]
    if target.is_dir():
        files = sorted(target.glob("*.yaml")) + sorted(target.glob("*.yml"))
        if not files:
            raise FileNotFoundError(f"No *.yaml task files in {target}")
        return [Task.from_yaml(f) for f in files]
    raise FileNotFoundError(target)


# ---------------------------------------------------------------------------
# Run record
# ---------------------------------------------------------------------------


@dataclass
class RunRecord:
    """One row in the JSONL output. One per (task, mode) execution."""
    run_id: str
    task_id: str
    task_title: str
    category: str           # task.category — for dashboard grouping
    mode: str               # "vanilla" | "vanilla-context" | "with-spaider"
    provider: str           # "openai" | "anthropic" | "ollama" | …
    model: str              # bare model id (no provider prefix)
    started_at: str         # ISO-8601 UTC
    wall_time_ms: float
    tokens_in: int
    tokens_out: int
    tool_calls: int
    success: bool
    final_text: str         # truncated to 2_000 chars
    # Reasoning models (DeepSeek-R1, Gemma 4B reasoning, o1, …) emit a
    # separate thinking trace before producing visible content. Captured
    # for debugging so the dashboard can show *why* a task failed even
    # when final_text is short or empty. Truncated to 2_000 chars.
    reasoning_text: str = ""
    # When the oracle is `llm_judge`, the judge's one-sentence verdict
    # rationale lands here. Empty for substring-oracle tasks.
    judge_rationale: str = ""
    # Oracle kind in effect for this run — useful in the dashboard
    # ("did substring or llm_judge decide this?").
    oracle_kind: str = "substring"
    # Feedback-loop telemetry. True when the runner echoed the
    # judge verdict to /api/v1/feedback so SpAIder's Hebbian utility_weight
    # actually updates. Always False outside --mode with-spaider.
    feedback_posted: bool = False
    feedback_node_count: int = 0
    # DeepEval-style metrics. Populated only when the task
    # uses the f1, exact_match, geval, or composite oracle. Float in
    # [0.0, 1.0]; None when the metric did not run.
    f1_score: Optional[float] = None
    exact_match: Optional[float] = None
    geval_score: Optional[float] = None
    # ROUGE-L (longest common subsequence) handles paraphrase
    # better than F1's strict token-overlap and is the new headline metric
    # for HotpotQA. Populated whenever the composite/f1/exact_match/geval
    # oracle ran (i.e. expected_output was present).
    rouge_l_score: Optional[float] = None
    # Retrieval-quality probe for with-spaider mode (hardened).
    # `retrieval_hit`: 1.0 only when the task's expected_output appears in the
    # spaider.query tool-response text — retrieval surfaced the *answer*.
    # Falls back to the subject metric for yes/no-style golds.
    # `retrieval_hit_subject`: the original lenient metric (fraction of
    # `properties.supporting_titles` present) — kept for comparability with
    # historical runs; it over-reports because titles are usually the
    # question's subject, which the backend's own answer text echoes.
    retrieval_hit: Optional[float] = None
    retrieval_hit_subject: Optional[float] = None
    # Node IDs surfaced by spaider.query during the tool-call loop
    # (already used internally for the feedback POST). Surfaced to the
    # JSONL so retrieval-quality forensics can be done off-line.
    retrieved_node_ids: list[str] = field(default_factory=list)
    # SpAIder's server-side LLM grounding spend, parsed from the
    # "Backend tokens: in=<n> out=<n>" trailer of each spaider.query tool
    # result and summed across the run. The agent-side tokens_in/out above are
    # only what the *calling* model spent; total_tokens_in/out add these so the
    # with-spaider mode reflects true end-to-end cost. Always 0 outside
    # with-spaider mode (no backend calls).
    backend_tokens_in: int = 0
    backend_tokens_out: int = 0
    error: Optional[str] = None

    @property
    def total_tokens_in(self) -> int:
        """Agent-side + SpAIder backend input tokens (true total cost)."""
        return (self.tokens_in or 0) + (self.backend_tokens_in or 0)

    @property
    def total_tokens_out(self) -> int:
        """Agent-side + SpAIder backend output tokens (true total cost)."""
        return (self.tokens_out or 0) + (self.backend_tokens_out or 0)


# ---------------------------------------------------------------------------
# Provider plumbing — mirror SpAIder's LiteLLM contract.
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class LLMConfig:
    provider: str
    model: str
    api_key: Optional[str]
    base_url: Optional[str]

    @property
    def litellm_model(self) -> str:
        """LiteLLM model identifier ``<provider>/<model>``.

        Special case: Ollama → ``ollama_chat/`` so tool calling routes to
        the chat completion endpoint (the bare ``ollama/`` prefix uses the
        legacy generate API which does not support tools).
        """
        if self.provider == "ollama":
            return f"ollama_chat/{self.model}"
        if self.provider == "openai":
            # LiteLLM accepts bare model names for OpenAI too; keep it
            # simple for the most common case.
            return self.model
        return f"{self.provider}/{self.model}"


def _resolve_llm_config(args: argparse.Namespace) -> LLMConfig:
    """CLI flags override env; env mirrors SpAIder's `.env` contract."""
    provider = (args.provider or os.environ.get("LLM_PROVIDER") or "").strip()
    model = (args.model or os.environ.get("LLM_MODEL") or "").strip()
    if not provider:
        raise SystemExit(
            "LLM_PROVIDER is not set. Either export it (e.g. "
            "`export LLM_PROVIDER=ollama`) or pass --provider."
        )
    if not model:
        raise SystemExit(
            "LLM_MODEL is not set. Either export it (e.g. "
            "`export LLM_MODEL=llama3.2:3b`) or pass --model."
        )
    api_key = os.environ.get("LLM_API_KEY") or None
    base_url = (os.environ.get("LLM_BASE_URL") or "").strip() or None
    # Ollama doesn't validate the key but LiteLLM expects something
    # truthy; substitute a placeholder so the user doesn't have to.
    if provider == "ollama" and not api_key:
        api_key = "ollama"
    return LLMConfig(provider=provider, model=model, api_key=api_key, base_url=base_url)


# ---------------------------------------------------------------------------
# MCP tool schema, expressed in OpenAI/LiteLLM tool format. Hard-coded so
# the runner doesn't need a live `tools/list` round-trip at start-up — keeps
# the vanilla mode runnable without a SpAIder stack at all.
#
# Tool names use underscores because OpenAI's tool name validator forbids
# dots; the runner translates ``spaider_query`` → ``spaider.query`` before
# the MCP call.
# ---------------------------------------------------------------------------


_MCP_TOOL_SCHEMAS: list[dict[str, Any]] = [
    {
        "type": "function",
        "function": {
            "name": "spaider_query",
            "description": (
                "Ask SpAIder a question. Searches the calling agent's "
                "knowledge graph and returns an LLM-generated answer plus a "
                "short summary of the supporting nodes."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "question": {"type": "string"},
                    "top_k": {"type": "integer", "default": 10},
                },
                "required": ["question"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spaider_list_recent",
            "description": (
                "List the most recently created knowledge nodes belonging to "
                "the calling agent. Useful as a session-start probe."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "limit": {"type": "integer", "default": 10},
                },
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "spaider_ingest_fact",
            "description": (
                "Write a fact into the calling agent's knowledge graph through "
                "the standard extraction pipeline. Use sparingly — only for "
                "non-obvious facts worth recalling later."
            ),
            "parameters": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "source": {"type": "string"},
                },
                "required": ["text"],
            },
        },
    },
]


# ---------------------------------------------------------------------------
# LiteLLM call helpers
# ---------------------------------------------------------------------------


def _completion_kwargs(cfg: LLMConfig, max_tokens: int) -> dict[str, Any]:
    kwargs: dict[str, Any] = {
        "model": cfg.litellm_model,
        "max_tokens": max_tokens,
        "temperature": 0.0,
    }
    if cfg.api_key:
        kwargs["api_key"] = cfg.api_key
    if cfg.base_url:
        kwargs["api_base"] = cfg.base_url
    return kwargs


def _extract_text(message: Any) -> str:
    """LiteLLM normalises providers to OpenAI shape — message.content is str|None."""
    return getattr(message, "content", None) or ""


def _extract_reasoning(message: Any) -> str:
    """Reasoning models route the chain-of-thought to a separate field.

    LiteLLM exposes it as ``reasoning_content`` (DeepSeek-R1, Gemma 4B
    reasoning, …). Empty string for non-reasoning models — uniform shape.
    """
    return getattr(message, "reasoning_content", None) or ""


def _extract_tool_calls(message: Any) -> list[Any]:
    return getattr(message, "tool_calls", None) or []


def _assistant_turn(message: Any, tool_calls: list[Any]) -> dict[str, Any]:
    """Reconstruct the assistant message we just received so we can append
    it back to ``messages`` for the next turn. Format is OpenAI-standard
    chat.completion message shape, which LiteLLM accepts uniformly."""
    turn: dict[str, Any] = {
        "role": "assistant",
        "content": _extract_text(message),
    }
    if tool_calls:
        turn["tool_calls"] = [
            {
                "id": tc.id,
                "type": "function",
                "function": {
                    "name": tc.function.name,
                    "arguments": tc.function.arguments,
                },
            }
            for tc in tool_calls
        ]
    return turn


# ---------------------------------------------------------------------------
# Mode: vanilla
# ---------------------------------------------------------------------------


async def _run_vanilla(
    task: Task, cfg: LLMConfig, *, system_prompt: Optional[str] = None,
    mode_label: str = "vanilla",
) -> RunRecord:
    """Plain LiteLLM completion, no tools.

    When ``system_prompt`` is supplied (e.g. the AcmeAI corpus dump for
    --mode vanilla-context), it is prepended as a system message. The
    mode label in the JSONL row is taken from ``mode_label``.
    """
    from litellm import acompletion

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    err: Optional[str] = None
    final_text = ""
    reasoning_text = ""
    tokens_in = tokens_out = 0

    messages: list[dict[str, Any]] = []
    if system_prompt:
        messages.append({"role": "system", "content": system_prompt})
    messages.append({"role": "user", "content": task.prompt})

    try:
        resp = await acompletion(
            messages=messages,
            **_completion_kwargs(cfg, task.max_tokens),
        )
        message = resp.choices[0].message
        final_text = _extract_text(message)
        reasoning_text = _extract_reasoning(message)
        usage = getattr(resp, "usage", None)
        if usage:
            tokens_in = getattr(usage, "prompt_tokens", 0) or 0
            tokens_out = getattr(usage, "completion_tokens", 0) or 0
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("%s run failed for %s: %s", mode_label, task.id, err)

    if err:
        result = EvalResult(False, "")
    else:
        result = await _evaluate(task, final_text, cfg)

    wall = (time.perf_counter() - t0) * 1000
    return RunRecord(
        run_id=str(uuid.uuid4()),
        task_id=task.id,
        task_title=task.title,
        category=task.category,
        mode=mode_label,
        provider=cfg.provider,
        model=cfg.model,
        started_at=started.isoformat(),
        wall_time_ms=round(wall, 2),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tool_calls=0,
        success=result.success,
        final_text=final_text[:2_000],
        reasoning_text=reasoning_text[:2_000],
        judge_rationale=result.rationale,
        oracle_kind=task.oracle_kind,
        f1_score=result.f1_score,
        exact_match=result.exact_match,
        geval_score=result.geval_score,
        rouge_l_score=result.rouge_l_score,
        # vanilla / vanilla-context modes have no MCP tool surface, so
        # retrieval_hit and retrieved_node_ids are intrinsically N/A.
        error=err,
    )


# ---------------------------------------------------------------------------
# Mode: with-spaider
# ---------------------------------------------------------------------------


# Default system prompt for --mode with-spaider. Without this
# gpt-4o-mini consistently skipped tool calls on HotpotQA bridge questions
# (Scott Derrickson, Ed Wood, …) and answered from training-set priors.
# Forensics across recent sweeps showed 6 of 7 chronic-fail tasks had
# tool_calls=0. This prompt makes retrieval mandatory; the format-hint
# (kept short) is appended *after* the first tool result so it doesn't
# suppress the initial tool call.
_DEFAULT_WITH_SPAIDER_SYSTEM_PROMPT = (
    "You answer questions using a private knowledge graph that contains "
    "the authoritative facts for this task. Always call `spaider_query` "
    "first with the user's question (or a focused sub-question) to retrieve "
    "grounded context. Do NOT answer from training-set knowledge — the gold "
    "facts may differ from what you remember. Only after you have called "
    "the tool and read the result should you compose the final answer."
)


async def _run_with_spaider(task: Task, cfg: LLMConfig) -> RunRecord:
    """LiteLLM completion with SpAIder MCP tools available. The runner
    bridges OpenAI-style tool_calls to the MCP server via mcp.client.sse."""
    from litellm import acompletion
    from mcp import ClientSession
    from mcp.client.sse import sse_client

    api_key = os.environ.get("SPAIDER_API_KEY")
    if not api_key:
        raise RuntimeError(
            "SPAIDER_API_KEY required for --mode with-spaider. "
            "Run scripts/dev/setup_mcp_dev_agent.sh to provision one."
        )
    mcp_url = os.environ.get(
        "SPAIDER_MCP_URL", "http://localhost:8001/api/v1/mcp/sse",
    )

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    err: Optional[str] = None
    final_text = ""
    reasoning_text = ""
    tokens_in = tokens_out = tool_calls = 0
    # SpAIder's server-side grounding spend, summed from the "Backend tokens"
    # trailer of every spaider.query result. Added to the agent tokens for the
    # true total cost the with-spaider mode incurs.
    backend_tokens_in = backend_tokens_out = 0
    # accumulate every node ID surfaced by spaider.query so we
    # can echo them back to /api/v1/feedback after the task completes.
    retrieved_ids: set[str] = set()

    # Concatenated tool-response text from every spaider.query call —
    # feeds the orthogonal retrieval_hit metric. Kept as a
    # single string (newline-joined) rather than a list so the metric
    # function can do one substring sweep.
    spaider_query_responses: list[str] = []

    # Resolve effective system prompt: per-task override wins; empty string
    # explicitly disables; otherwise the with-spaider default fires (issue
    # #104).
    if task.system_prompt is None:
        effective_system_prompt = _DEFAULT_WITH_SPAIDER_SYSTEM_PROMPT
    else:
        effective_system_prompt = task.system_prompt
    # Tracks whether the format-hint has already been injected. We append
    # it once, the first time the model returns a tool-result-bearing
    # message, so it shapes the *answer* without suppressing the initial
    # spaider.query call.
    format_hint_injected = False

    try:
        async with sse_client(mcp_url, headers={"Authorization": f"Bearer {api_key}"}) as streams:
            async with ClientSession(*streams) as mcp_session:
                await mcp_session.initialize()

                messages: list[dict[str, Any]] = []
                if effective_system_prompt.strip():
                    messages.append({
                        "role": "system",
                        "content": effective_system_prompt,
                    })
                messages.append({"role": "user", "content": task.prompt})
                for _ in range(8):  # bounded — guards against infinite loops
                    resp = await acompletion(
                        messages=messages,
                        tools=_MCP_TOOL_SCHEMAS,
                        **_completion_kwargs(cfg, task.max_tokens),
                    )
                    usage = getattr(resp, "usage", None)
                    if usage:
                        tokens_in += getattr(usage, "prompt_tokens", 0) or 0
                        tokens_out += getattr(usage, "completion_tokens", 0) or 0

                    choice = resp.choices[0]
                    message = choice.message
                    requested_tools = _extract_tool_calls(message)

                    if not requested_tools:
                        final_text = _extract_text(message)
                        reasoning_text = _extract_reasoning(message)
                        break

                    messages.append(_assistant_turn(message, requested_tools))

                    for tc in requested_tools:
                        tool_calls += 1
                        # Anthropic/OpenAI both forbid dots in tool names;
                        # MCP exposes dotted names. Translate first dot.
                        mcp_name = tc.function.name.replace("_", ".", 1)
                        try:
                            raw_args = tc.function.arguments or "{}"
                            tool_input = json.loads(raw_args) if isinstance(raw_args, str) else raw_args
                        except json.JSONDecodeError:
                            tool_input = {}
                        try:
                            mcp_result = await mcp_session.call_tool(mcp_name, tool_input or {})
                            text_blocks = [
                                getattr(c, "text", str(c)) for c in mcp_result.content
                            ]
                            payload = "\n".join(text_blocks)
                        except Exception as exc:  # noqa: BLE001
                            payload = f"(tool error) {type(exc).__name__}: {exc}"
                        # Harvest node IDs from spaider.query responses for
                        # the post-task feedback POST. Other tools (ingest,
                        # list_recent) don't include the trailer.
                        if mcp_name == "spaider.query":
                            retrieved_ids.update(_parse_node_ids_trailer(payload))
                            bt_in, bt_out = _parse_backend_tokens_trailer(payload)
                            backend_tokens_in += bt_in
                            backend_tokens_out += bt_out
                            # Capture the full payload for retrieval_hit
                            # Includes the synthesised answer,
                            # supporting facts, and supporting entities — the
                            # places gold paragraph titles would appear.
                            spaider_query_responses.append(payload)
                        messages.append({
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": payload,
                        })

                    # Inject the format hint *once*, after the first batch of
                    # tool results lands but before the next completion. By
                    # this point the model has already chosen to use tools;
                    # the hint can safely shape the answer-shape without
                    # re-suppressing retrieval.
                    if task.format_hint and not format_hint_injected:
                        messages.append({
                            "role": "user",
                            "content": task.format_hint,
                        })
                        format_hint_injected = True
                else:
                    # Tool budget exhausted while the model was still calling
                    # tools. Rather than emit a 0-score non-answer, force ONE
                    # final tool-less completion so it answers from everything
                    # gathered so far.
                    if task.format_hint and not format_hint_injected:
                        messages.append({"role": "user", "content": task.format_hint})
                    messages.append({
                        "role": "user",
                        "content": (
                            "You have used your tool budget. Answer the original "
                            "question now using the information gathered above. "
                            "Do not call any more tools."
                        ),
                    })
                    final_resp = await acompletion(
                        messages=messages,
                        **_completion_kwargs(cfg, task.max_tokens),
                    )
                    fu = getattr(final_resp, "usage", None)
                    if fu:
                        tokens_in += getattr(fu, "prompt_tokens", 0) or 0
                        tokens_out += getattr(fu, "completion_tokens", 0) or 0
                    final_text = (
                        _extract_text(final_resp.choices[0].message)
                        or "(no answer produced after tool budget)"
                    )
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("with-spaider run failed for %s: %s", task.id, err)

    if err:
        result = EvalResult(False, "")
    else:
        result = await _evaluate(
            task, final_text, cfg,
            retrieved_text="\n".join(spaider_query_responses) or None,
        )

    # Close the feedback loop. Skipped on errors and when the
    # model never invoked spaider.query — neither case carries a usable
    # signal for Hebbian utility_weight updates.
    feedback_posted = False
    if not err and retrieved_ids:
        feedback_posted = await _post_feedback(
            mcp_url=mcp_url, api_key=api_key,
            used_node_ids=list(retrieved_ids), success=result.success,
        )

    wall = (time.perf_counter() - t0) * 1000
    return RunRecord(
        run_id=str(uuid.uuid4()),
        task_id=task.id,
        task_title=task.title,
        category=task.category,
        mode="with-spaider",
        provider=cfg.provider,
        model=cfg.model,
        started_at=started.isoformat(),
        wall_time_ms=round(wall, 2),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tool_calls=tool_calls,
        success=result.success,
        final_text=final_text[:2_000],
        reasoning_text=reasoning_text[:2_000],
        judge_rationale=result.rationale,
        oracle_kind=task.oracle_kind,
        feedback_posted=feedback_posted,
        feedback_node_count=len(retrieved_ids),
        f1_score=result.f1_score,
        exact_match=result.exact_match,
        geval_score=result.geval_score,
        rouge_l_score=result.rouge_l_score,
        retrieval_hit=result.retrieval_hit,
        retrieval_hit_subject=result.retrieval_hit_subject,
        retrieved_node_ids=sorted(retrieved_ids),
        backend_tokens_in=backend_tokens_in,
        backend_tokens_out=backend_tokens_out,
        error=err,
    )


# ---------------------------------------------------------------------------
# Mode: generic adapter (head-to-head competitors — Mem0, Cognee, spaider-fixed)
# ---------------------------------------------------------------------------


# Shared fixed-reader prompt. EVERY system's fixed-reader run uses this exact
# reader, so the only thing that differs across arms is the retrieved context —
# isolating the memory's contribution from the answer model. Mirrors the intent
# of _DEFAULT_WITH_SPAIDER_SYSTEM_PROMPT (ground in retrieved facts, not priors).
_FIXED_READER_SYSTEM = (
    "You answer the user's question using ONLY the retrieved context below. "
    "It contains the authoritative facts for this task; do not rely on prior "
    "knowledge, because the gold facts may differ from what you remember. "
    "Answer directly and concisely.\n\n"
    "=== Retrieved context ===\n{context}\n=== End retrieved context ==="
)


async def run_with_adapter(
    adapter: "MemorySystemAdapter", task: Task, cfg: LLMConfig, answer_mode: str,
) -> RunRecord:
    """Run one task through a competitor memory system (Mem0, Cognee, …).

    ``answer_mode``:
      * ``"fixed"``  — ``adapter.retrieve()`` → the shared fixed reader answers
        (same reader model for every system; only retrieval differs).
      * ``"native"`` — ``adapter.answer_native()`` answers end-to-end.

    Returns a RunRecord with ``mode = f"{adapter.name}-{answer_mode}"`` so it
    lands beside vanilla / with-spaider rows in the same JSONL and scorecard.
    Adapter-agnostic: it only touches the ``MemorySystemAdapter`` interface, so
    it never imports mem0/cognee — those load only inside the concrete adapter.
    """
    from litellm import acompletion

    if answer_mode not in ("fixed", "native"):
        raise ValueError(f"answer_mode must be 'fixed' or 'native', got {answer_mode!r}")

    started = datetime.now(timezone.utc)
    t0 = time.perf_counter()
    err: Optional[str] = None
    final_text = ""
    tokens_in = tokens_out = 0
    retrieved_text: Optional[str] = None

    # The question the system sees. Append the per-task format hint so answers
    # stay short — the same hint the vanilla / with-spaider arms receive.
    question = task.prompt
    if task.format_hint:
        question = f"{task.prompt}\n\n{task.format_hint}"

    try:
        if answer_mode == "fixed":
            ctx = await adapter.retrieve(task.prompt)
            retrieved_text = ctx.text or None
            messages = [
                {"role": "system", "content": _FIXED_READER_SYSTEM.format(context=ctx.text)},
                {"role": "user", "content": question},
            ]
            resp = await acompletion(
                messages=messages, **_completion_kwargs(cfg, task.max_tokens),
            )
            message = resp.choices[0].message
            final_text = _extract_text(message)
            usage = getattr(resp, "usage", None)
            if usage:
                tokens_in = getattr(usage, "prompt_tokens", 0) or 0
                tokens_out = getattr(usage, "completion_tokens", 0) or 0
        else:  # native
            ans = await adapter.answer_native(question, task.max_tokens)
            final_text = ans.text or ""
            tokens_in = ans.tokens_in
            tokens_out = ans.tokens_out
            retrieved_text = ans.retrieved_text
    except Exception as exc:  # noqa: BLE001
        err = f"{type(exc).__name__}: {exc}"
        logger.warning("%s-%s run failed for %s: %s", adapter.name, answer_mode, task.id, err)

    if err:
        result = EvalResult(False, "")
    else:
        result = await _evaluate(task, final_text, cfg, retrieved_text=retrieved_text)

    wall = (time.perf_counter() - t0) * 1000
    return RunRecord(
        run_id=str(uuid.uuid4()),
        task_id=task.id,
        task_title=task.title,
        category=task.category,
        mode=f"{adapter.name}-{answer_mode}",
        provider=cfg.provider,
        model=cfg.model,
        started_at=started.isoformat(),
        wall_time_ms=round(wall, 2),
        tokens_in=tokens_in,
        tokens_out=tokens_out,
        tool_calls=0,
        success=result.success,
        final_text=final_text[:2_000],
        judge_rationale=result.rationale,
        oracle_kind=task.oracle_kind,
        f1_score=result.f1_score,
        exact_match=result.exact_match,
        geval_score=result.geval_score,
        rouge_l_score=result.rouge_l_score,
        retrieval_hit=result.retrieval_hit,
        retrieval_hit_subject=result.retrieval_hit_subject,
        error=err,
    )


# ---------------------------------------------------------------------------
# Feedback loop — echo LLM-judge verdicts to /api/v1/feedback so
# Hebbian utility_weight updates fire on every successful query.
# ---------------------------------------------------------------------------


def _parse_node_ids_trailer(text: str) -> list[str]:
    """Extract node IDs from the ``Node IDs (for feedback): id1, id2, ...``
    line that ``spaider.query`` appends to its response. Returns [] if the
    line is absent (caller will skip the feedback POST in that case)."""
    for line in (text or "").splitlines():
        if line.startswith("Node IDs (for feedback):"):
            ids_part = line.split(":", 1)[1].strip()
            return [s.strip() for s in ids_part.split(",") if s.strip()]
    return []


def _parse_backend_tokens_trailer(text: str) -> tuple[int, int]:
    """Extract ``(in, out)`` from the ``Backend tokens: in=<n> out=<n>`` line
    that ``spaider.query`` appends. Returns ``(0, 0)`` if absent (older backend
    or a non-query tool) so the caller can sum unconditionally."""
    for line in (text or "").splitlines():
        if line.startswith("Backend tokens:"):
            bt_in = bt_out = 0
            for tok in line.split(":", 1)[1].split():
                key, _, val = tok.partition("=")
                if key == "in" and val.isdigit():
                    bt_in = int(val)
                elif key == "out" and val.isdigit():
                    bt_out = int(val)
            return bt_in, bt_out
    return 0, 0


def _feedback_url_from_mcp(mcp_url: str) -> str:
    """``http://host/api/v1/mcp/sse`` → ``http://host/api/v1/system/feedback``.

    The feedback router is mounted under ``/system`` in
    ``backend/app/api/router.py`` (``include_router(feedback.router,
    prefix='/system', ...)``) so its full path is ``/api/v1/system/feedback``,
    NOT ``/api/v1/feedback``.
    """
    return mcp_url.replace("/mcp/sse", "/system/feedback")


async def _post_feedback(
    *, mcp_url: str, api_key: str, used_node_ids: list[str], success: bool,
) -> bool:
    """POST a feedback signal. Returns True when the server accepted (HTTP
    2xx); False on any failure. Failure is non-fatal — the benchmark run
    is already done; we just log and move on."""
    import httpx

    url = _feedback_url_from_mcp(mcp_url)
    payload = {
        "query_id": str(uuid.uuid4()),
        "used_node_ids": used_node_ids[:200],  # match server-side dedupe cap
        "success": success,
    }
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {api_key}"},
            )
            if 200 <= resp.status_code < 300:
                return True
            logger.warning(
                "feedback POST returned %s: %s",
                resp.status_code, resp.text[:200],
            )
            return False
    except Exception as exc:  # noqa: BLE001
        logger.warning("feedback POST failed: %s: %s", type(exc).__name__, exc)
        return False


# ---------------------------------------------------------------------------
# Judging — two oracle kinds. See benchmarks/README.md §Evaluation.
# ---------------------------------------------------------------------------


@dataclass
class EvalResult:
    """Outcome of evaluating a task's answer. Carries the binary
    success flag plus any continuous DeepEval-style metric scores
    that ran (None when the metric was not part of the oracle)."""
    success: bool
    rationale: str = ""
    f1_score: Optional[float] = None
    exact_match: Optional[float] = None
    geval_score: Optional[float] = None
    rouge_l_score: Optional[float] = None
    retrieval_hit: Optional[float] = None
    retrieval_hit_subject: Optional[float] = None


async def _evaluate(
    task: Task,
    final_text: str,
    cfg: LLMConfig,
    *,
    retrieved_text: Optional[str] = None,
) -> EvalResult:
    """Dispatch to the configured oracle. See ``Task`` docstring for the
    oracle kinds. Returns an EvalResult — callers extract the fields
    they care about for their RunRecord.

    ``retrieved_text`` is the concatenated tool-response text from
    ``spaider.query`` calls during a with-spaider run (or ``None`` for
    vanilla / vanilla-context runs). It feeds the orthogonal
    retrieval_hit metric without affecting any other oracle.
    """
    kind = task.oracle_kind
    # Retrieval metrics are independent of the oracle kind. Computed only
    # for runs that actually called the tool (retrieved_text is not None).
    if retrieved_text is not None:
        retrieval_hit, retrieval_hit_subject = _compute_retrieval_hits(retrieved_text, task)
    else:
        retrieval_hit, retrieval_hit_subject = None, None

    if kind == "llm_judge":
        if not task.oracle_rubric:
            return EvalResult(False, "(oracle=llm_judge but rubric is empty)", retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)
        passed, rationale = await _judge_llm(final_text, task, cfg)
        return EvalResult(passed, rationale, retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)

    if kind == "f1":
        if task.expected_output is None:
            return EvalResult(False, "(oracle=f1 but expected_output is empty)", retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)
        f1 = _compute_f1(final_text, task.expected_output)
        rl = _compute_rouge_l(final_text, task.expected_output)
        return EvalResult(f1 >= 0.5, f"f1={f1:.2f} rouge_l={rl:.2f}",
                          f1_score=f1, rouge_l_score=rl, retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)

    if kind == "exact_match":
        if task.expected_output is None:
            return EvalResult(False, "(oracle=exact_match but expected_output is empty)", retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)
        em = _compute_exact_match(final_text, task.expected_output)
        return EvalResult(bool(em), f"em={em:.0f}", exact_match=em, retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)

    if kind == "geval":
        if task.expected_output is None:
            return EvalResult(False, "(oracle=geval but expected_output is empty)", retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)
        score, rationale = await _judge_geval(final_text, task, cfg)
        return EvalResult(score >= 0.5, rationale, geval_score=score, retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)

    if kind == "composite":
        # DeepEval-style multi-metric scoring on a single ground-truth
        # answer. Run all four token-level metrics + the LLM judge; mark
        # `success` if either EM or GEval passes (continuous metrics are
        # what callers actually compare — `success` is just for the
        # legacy pass-rate display).
        if task.expected_output is None:
            return EvalResult(False, "(oracle=composite but expected_output is empty)", retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)
        f1 = _compute_f1(final_text, task.expected_output)
        em = _compute_exact_match(final_text, task.expected_output)
        rl = _compute_rouge_l(final_text, task.expected_output)
        ge_score, ge_rationale = await _judge_geval(final_text, task, cfg)
        passed = bool(em) or ge_score >= 0.5
        rationale = f"f1={f1:.2f} rouge_l={rl:.2f} em={em:.0f} geval={ge_score:.2f}"
        if ge_rationale:
            rationale += f" — {ge_rationale}"
        return EvalResult(
            passed, rationale,
            f1_score=f1, exact_match=em, geval_score=ge_score,
            rouge_l_score=rl, retrieval_hit=retrieval_hit,
            retrieval_hit_subject=retrieval_hit_subject,
        )

    # Default: substring (v1 behaviour, unchanged).
    return EvalResult(_judge_substring(final_text, task), "", retrieval_hit=retrieval_hit,
                          retrieval_hit_subject=retrieval_hit_subject)


def _judge_substring(final_text: str, task: Task) -> bool:
    """True iff the assistant's final text contains all expected substrings."""
    text = (final_text or "").lower()
    if task.expected_substring and task.expected_substring.lower() not in text:
        return False
    for needle in task.expected_all:
        if needle.lower() not in text:
            return False
    return bool(text.strip())


# ---------------------------------------------------------------------------
# DeepEval-style QA metrics. F1 and exact_match follow the
# normalisation in HotpotQA's official eval script (lowercase, strip
# punctuation, drop articles, collapse whitespace, then token-overlap
# F1 / set-equality EM). Pure Python — no external dependency.
# ---------------------------------------------------------------------------


def _normalize_qa_text(s: str) -> str:
    """HotpotQA-style normalisation: lowercase, strip punctuation, drop
    English articles, collapse whitespace."""
    import re
    import string
    s = (s or "").lower()
    s = "".join(ch for ch in s if ch not in set(string.punctuation))
    # Drop articles a/an/the as standalone words
    s = re.sub(r"\b(a|an|the)\b", " ", s)
    return " ".join(s.split())


def _compute_f1(prediction: str, expected: str) -> float:
    """Token-overlap F1 score (HotpotQA reference impl). Returns a float
    in [0.0, 1.0]. 1.0 when prediction tokens == expected tokens; 0.0
    when there is no overlap."""
    pred_tokens = _normalize_qa_text(prediction).split()
    gold_tokens = _normalize_qa_text(expected).split()
    if not pred_tokens or not gold_tokens:
        # F1 is 1.0 only if both are empty; otherwise 0.0
        return float(pred_tokens == gold_tokens)
    common: dict[str, int] = {}
    for t in pred_tokens:
        if t in gold_tokens:
            common[t] = common.get(t, 0) + 1
    num_same = sum(common.values())
    if num_same == 0:
        return 0.0
    precision = num_same / len(pred_tokens)
    recall = num_same / len(gold_tokens)
    return 2.0 * precision * recall / (precision + recall)


def _compute_exact_match(prediction: str, expected: str) -> float:
    """Normalised exact-match. Returns 1.0 on equality after
    normalisation, 0.0 otherwise."""
    return float(_normalize_qa_text(prediction) == _normalize_qa_text(expected))


def _compute_rouge_l(prediction: str, expected: str) -> float:
    """ROUGE-L F-measure (longest common subsequence based).

    Tolerates paraphrase / extra padding tokens — for "Yes, both were
    American directors" against gold "Yes", LCS picks up the matching
    token cleanly without penalising the surrounding words.
    intended to replace F1 as the headline HotpotQA metric.

    Reference: ROUGE-L from `Lin (2004) "ROUGE: A Package for Automatic
    Evaluation of Summaries"`. We reuse `_normalize_qa_text` so the
    normalisation is identical to F1/EM.
    """
    pred_tokens = _normalize_qa_text(prediction).split()
    gold_tokens = _normalize_qa_text(expected).split()
    if not pred_tokens or not gold_tokens:
        return float(pred_tokens == gold_tokens)

    # Standard LCS DP. O(len(pred) × len(gold)); fine for our 1-50 token
    # answer length.
    m, n = len(pred_tokens), len(gold_tokens)
    dp = [[0] * (n + 1) for _ in range(m + 1)]
    for i in range(1, m + 1):
        for j in range(1, n + 1):
            if pred_tokens[i - 1] == gold_tokens[j - 1]:
                dp[i][j] = dp[i - 1][j - 1] + 1
            else:
                dp[i][j] = max(dp[i - 1][j], dp[i][j - 1])
    lcs = dp[m][n]
    if lcs == 0:
        return 0.0
    precision = lcs / m
    recall = lcs / n
    return 2.0 * precision * recall / (precision + recall)


def _compute_retrieval_hits(
    retrieved_text: Optional[str], task: Task,
) -> tuple[Optional[float], Optional[float]]:
    """Return ``(retrieval_hit, retrieval_hit_subject)`` for a with-spaider run.

    ``retrieval_hit_subject`` is the original #105 metric: the fraction of the
    task's ``properties.supporting_titles`` that appear (case-insensitive
    substring) anywhere in the spaider.query tool-response text. It turned out
    to be too lenient — titles are usually the question's *subject* entities,
    and the backend's answer often echoes the question's own words, so the
    metric reported 100% while the answer-bearing node was never retrieved.

    ``retrieval_hit`` is the honest replacement: 1.0 only when the task's
    ``expected_output`` itself appears in the retrieved text — i.e. retrieval
    actually surfaced the answer, not just the subject. Falls back to the
    subject metric for tasks where answer containment is meaningless
    (yes/no or very short golds), and to None when neither signal applies.
    """
    titles = task.properties.get("supporting_titles") if task.properties else None
    has_titles = bool(titles) and isinstance(titles, list)

    subject: Optional[float] = None
    if has_titles:
        if not retrieved_text:
            subject = 0.0
        else:
            haystack = retrieved_text.lower()
            found = sum(
                1 for t in titles
                if isinstance(t, str) and t.strip() and t.lower() in haystack
            )
            subject = found / len(titles)

    expected = (task.expected_output or "").strip()
    answer_checkable = len(expected) >= 3 and expected.lower() not in ("yes", "no")
    if answer_checkable:
        if not retrieved_text:
            honest: Optional[float] = 0.0
        else:
            honest = 1.0 if expected.lower() in retrieved_text.lower() else 0.0
    else:
        honest = subject

    return honest, subject


_GEVAL_PROMPT_TEMPLATE = """\
You are a strict, neutral grader. Compare the candidate answer to the
ground-truth answer for factual correctness.

Follow these evaluation steps exactly:
1. Identify the core fact(s) the ground-truth answer asserts (entity,
   number, date, or short phrase).
2. Check whether the candidate states the same fact(s) — names, numbers,
   dates and units must match; paraphrase and extra detail are fine.
3. Judge ONLY against the ground truth above. Do not use your own
   knowledge of the topic, even if you believe the ground truth is wrong.
4. Ignore verbosity, formatting, hedging and politeness.

Award a continuous score in [0.0, 1.0]:

  1.0 — candidate states the same fact as ground truth (paraphrase OK)
  0.5 — partially correct (some facts right, some wrong or missing)
  0.0 — wrong, contradicts ground truth, or evades

Question:
{question}

Ground-truth answer:
{expected}

Candidate answer:
{answer}

Reply with a single line of JSON, no preamble, no code fence:
{{"score": <float 0..1>, "rationale": "<one short sentence>"}}
"""


async def _judge_geval(
    final_text: str, task: Task, cfg: LLMConfig,
) -> tuple[float, str]:
    """LLM-as-judge graded against ``expected_output``. Mirrors DeepEval's
    GEval-correctness metric — continuous score in [0.0, 1.0]. Reuses the
    same model as the run (self-consistency, documented bias caveat)."""
    from litellm import acompletion

    if not (final_text or "").strip():
        return 0.0, "(empty answer)"

    prompt = _GEVAL_PROMPT_TEMPLATE.format(
        question=task.prompt.strip(),
        expected=(task.expected_output or "").strip(),
        answer=(final_text or "").strip(),
    )
    try:
        resp = await acompletion(
            messages=[{"role": "user", "content": prompt}],
            **_completion_kwargs(cfg, max_tokens=400),
        )
        raw = (_extract_text(resp.choices[0].message) or "").strip()
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            return 0.0, f"(judge non-JSON: {raw[:120]!r})"
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return 0.0, f"(judge bad JSON: {raw[start : end + 1][:120]!r})"
        score = float(parsed.get("score", 0.0))
        score = max(0.0, min(1.0, score))
        rationale = str(parsed.get("rationale", "")).strip()[:300]
        return score, rationale
    except Exception as exc:  # noqa: BLE001
        return 0.0, f"(judge error) {type(exc).__name__}: {exc}"


_JUDGE_PROMPT_TEMPLATE = """\
You are a strict, neutral evaluator. Decide whether the answer below
satisfies the rubric. Read the rubric literally and award PASS only if
the rubric's criteria are met. If you are unsure, FAIL.

Question:
{question}

Answer:
{answer}

Rubric:
{rubric}

Reply with a single line of JSON, no preamble, no code fence:
{{"verdict": "pass" | "fail", "rationale": "<one short sentence>"}}
"""


async def _judge_llm(final_text: str, task: Task, cfg: LLMConfig) -> tuple[bool, str]:
    """Grade the response against the task's rubric using a separate LLM call.

    The judge is the same model the run used (same provider config) — keeps
    the bill on one account and makes self-consistency the implicit norm.
    Bias caveat documented in benchmarks/README.md.
    """
    from litellm import acompletion

    if not (final_text or "").strip():
        return False, "(empty answer)"

    judge_prompt = _JUDGE_PROMPT_TEMPLATE.format(
        question=task.prompt.strip(),
        answer=(final_text or "").strip(),
        rubric=(task.oracle_rubric or "").strip(),
    )
    try:
        resp = await acompletion(
            messages=[{"role": "user", "content": judge_prompt}],
            **_completion_kwargs(cfg, max_tokens=400),
        )
        raw = (_extract_text(resp.choices[0].message) or "").strip()
        # Reasoning models may wrap the JSON in prose. Find the first {…}.
        start = raw.find("{")
        end = raw.rfind("}")
        if start == -1 or end == -1 or end < start:
            return False, f"(judge non-JSON: {raw[:120]!r})"
        try:
            parsed = json.loads(raw[start : end + 1])
        except json.JSONDecodeError:
            return False, f"(judge bad JSON: {raw[start : end + 1][:120]!r})"
        verdict = str(parsed.get("verdict", "")).strip().lower() == "pass"
        rationale = str(parsed.get("rationale", "")).strip()[:300]
        return verdict, rationale
    except Exception as exc:  # noqa: BLE001
        return False, f"(judge error) {type(exc).__name__}: {exc}"


# ---------------------------------------------------------------------------
# Output
# ---------------------------------------------------------------------------


def _runs_path(runs_dir: Path, provider: str, model: str) -> Path:
    """Date-stamped JSONL per day per (provider, model) — easy to wipe."""
    runs_dir.mkdir(parents=True, exist_ok=True)
    date = datetime.now(timezone.utc).strftime("%Y-%m-%d")
    safe_model = model.replace("/", "_").replace(":", "_")
    return runs_dir / f"{date}_{provider}_{safe_model}.jsonl"


def _append_record(record: RunRecord, runs_dir: Path) -> None:
    target = _runs_path(runs_dir, record.provider, record.model)
    with target.open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(asdict(record)) + "\n")


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


async def _run_one(
    task: Task, mode: str, cfg: LLMConfig, *, context: Optional[str] = None,
) -> RunRecord:
    if mode == "vanilla":
        return await _run_vanilla(task, cfg, mode_label="vanilla")
    if mode == "vanilla-context":
        if not context:
            raise ValueError("--mode vanilla-context requires --context-file")
        return await _run_vanilla(
            task, cfg, system_prompt=context, mode_label="vanilla-context",
        )
    if mode == "with-spaider":
        return await _run_with_spaider(task, cfg)
    raise ValueError(f"unknown mode: {mode!r}")


async def _run_main(args: argparse.Namespace) -> int:
    cfg = _resolve_llm_config(args)
    tasks = load_tasks(Path(args.tasks))
    runs_dir = Path(args.runs)
    context: Optional[str] = None
    if args.mode == "vanilla-context":
        if not args.context_file:
            print(
                "error: --mode vanilla-context requires --context-file PATH",
                file=sys.stderr,
            )
            return 2
        context = Path(args.context_file).read_text(encoding="utf-8")
        print(
            f"loaded context-file ({len(context)} chars) "
            f"from {args.context_file}",
            file=sys.stderr,
        )

    print(
        f"running {len(tasks)} task(s) in mode={args.mode} "
        f"provider={cfg.provider} model={cfg.model}",
        file=sys.stderr,
    )
    failed = 0
    for task in tasks:
        record = await _run_one(task, args.mode, cfg, context=context)
        _append_record(record, runs_dir)
        status = "OK " if record.success else "FAIL"
        if record.error:
            status = "ERR "
        print(
            f"  [{status}] {task.id:30s}  {record.wall_time_ms:>7.0f}ms  "
            f"tokens_in={record.tokens_in:>5} out={record.tokens_out:>4}  "
            f"tools={record.tool_calls}",
            file=sys.stderr,
        )
        if not record.success:
            failed += 1

    print(f"done: {len(tasks) - failed}/{len(tasks)} succeeded", file=sys.stderr)
    return 0 if failed == 0 else 1


def main() -> int:
    p = argparse.ArgumentParser(description="SpAIder benchmark runner")
    p.add_argument("--tasks", required=True, help="task YAML file or directory of them")
    p.add_argument(
        "--mode",
        choices=["vanilla", "vanilla-context", "with-spaider"],
        required=True,
        help=(
            "vanilla         = no tools, empty context. "
            "vanilla-context = no tools, full corpus injected as system prompt. "
            "with-spaider        = SpAIder MCP tools available."
        ),
    )
    p.add_argument(
        "--context-file", default=None,
        help="(--mode vanilla-context only) path to the corpus dump to inject "
             "as system prompt. e.g. benchmarks/corpus/acmeai_30d.txt",
    )
    p.add_argument(
        "--provider", default=None,
        help="LLM provider (default: $LLM_PROVIDER). e.g. openai, anthropic, ollama",
    )
    p.add_argument(
        "--model", default=None,
        help="Model id (default: $LLM_MODEL). e.g. gpt-4o-mini, llama3.2:3b",
    )
    p.add_argument("--runs", default="benchmarks/runs", help="JSONL output dir")
    p.add_argument(
        "--log-level", default="INFO",
        choices=["DEBUG", "INFO", "WARNING", "ERROR"],
    )
    args = p.parse_args()

    logging.basicConfig(
        level=args.log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    return asyncio.run(_run_main(args))


if __name__ == "__main__":
    sys.exit(main())
