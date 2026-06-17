"""
Streamlit dashboard for the SpAIder benchmark harness.

Reads JSONL run records from ``benchmarks/runs/`` and renders three views:

  1. Latest summary      — success rate / cost / latency per mode, plus the
                           with-spaider lift, grouped by category.
  2. DeepEval scorecard  — F1 / EM / GEval per (stack, mode, corpus), so the
                           public (HotpotQA) vs private (AcmeAI) split is read
                           directly off the table.
  3. Per-task detail     — final answer + reasoning + judge rationale.

The default view compares **vanilla** (model alone) vs **with-spaider** on the
**hotpotqa** (public) and **acmeai** (private) corpora — the latest clean test.
Untick filters in the sidebar to widen the view.

Run with::

    pip install -e benchmarks[dashboard]
    streamlit run benchmarks/dashboard.py
"""
from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import streamlit as st

RUNS_DIR = Path(__file__).parent / "runs"

# The two-arm, two-corpus story the dashboard defaults to.
DEFAULT_MODES = ["vanilla", "with-spaider"]
DEFAULT_CATEGORIES = ["hotpotqa", "acmeai"]


# Approximate $/M-token prices for the cost-per-query estimate. Prices are
# (in_per_million, out_per_million) in USD; override in the runner if a model
# isn't listed.
DEFAULT_PRICES_USD_PER_M: dict[tuple[str, str], tuple[float, float]] = {
    ("anthropic", "claude-haiku-4-5"):    (1.00, 5.00),
    ("anthropic", "claude-sonnet-4-6"):   (3.00, 15.00),
    ("anthropic", "claude-opus-4-8"):    (15.00, 75.00),
    ("openai", "gpt-4o-mini"):            (0.15, 0.60),
    ("openai", "gpt-4o"):                 (2.50, 10.00),
    ("ollama", "llama3.2:3b"):            (0.00, 0.00),
}


def _cost_usd(row: pd.Series) -> float:
    """Cost of one run, priced on TRUE total tokens.

    Total = agent-side tokens (what the calling model spent) + SpAIder's
    server-side grounding tokens (decomposition/synthesis/verify), priced at
    the same model rate. For vanilla modes the backend columns are 0, so this
    reduces to the agent-only cost — making the three modes directly comparable
    on real end-to-end spend, not just the half the agent sees.
    """
    key = (row.get("provider", ""), row.get("model", ""))
    prices = DEFAULT_PRICES_USD_PER_M.get(key, (0.0, 0.0))
    tin = (row.get("tokens_in", 0) or 0) + (row.get("backend_tokens_in", 0) or 0)
    tout = (row.get("tokens_out", 0) or 0) + (row.get("backend_tokens_out", 0) or 0)
    return tin / 1_000_000 * prices[0] + tout / 1_000_000 * prices[1]


@st.cache_data(ttl=30)
def _load_runs(runs_dir: Path) -> pd.DataFrame:
    rows: list[dict] = []
    for fp in sorted(runs_dir.glob("*.jsonl")):
        for line in fp.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line:
                continue
            try:
                rows.append(json.loads(line))
            except json.JSONDecodeError:
                continue
    if not rows:
        return pd.DataFrame()
    df = pd.DataFrame(rows)
    df["started_at"] = pd.to_datetime(df["started_at"], utc=True)
    df["date"] = df["started_at"].dt.date
    if "category" not in df.columns:
        df["category"] = "default"
    df["category"] = df["category"].fillna("default")
    # Backfill the backend-token columns absent from runs predating token
    # observability, so the cost math and totals treat them as zero-cost.
    for col in ("backend_tokens_in", "backend_tokens_out"):
        if col not in df.columns:
            df[col] = 0
        df[col] = df[col].fillna(0)
    # True total tokens = agent-side + SpAIder backend grounding.
    df["total_tokens_in"] = df["tokens_in"].fillna(0) + df["backend_tokens_in"]
    df["total_tokens_out"] = df["tokens_out"].fillna(0) + df["backend_tokens_out"]
    df["cost_usd"] = df.apply(_cost_usd, axis=1)
    # Backfill metric columns that may be absent from older runs.
    for col in ("f1_score", "exact_match", "geval_score"):
        if col not in df.columns:
            df[col] = pd.NA
    return df.sort_values("started_at").reset_index(drop=True)


def _latest_summary(
    df: pd.DataFrame, *, group_by_category: bool, group_by_model: bool = True,
) -> pd.DataFrame:
    """Latest result per (task, mode, [model])."""
    if df.empty:
        return df
    latest_keys = ["task_id", "mode"]
    if group_by_model:
        latest_keys += ["provider", "model"]
    latest = df.sort_values("started_at").groupby(latest_keys).tail(1)
    keys: list[str] = []
    if group_by_model:
        keys += ["provider", "model"]
    keys.append("mode")
    if group_by_category:
        keys.append("category")
    summary = (
        latest.groupby(keys)
        .agg(
            runs=("run_id", "count"),
            success_rate=("success", "mean"),
            avg_tokens_in=("tokens_in", "mean"),
            avg_tokens_out=("tokens_out", "mean"),
            avg_backend_tokens_in=("backend_tokens_in", "mean"),
            avg_backend_tokens_out=("backend_tokens_out", "mean"),
            avg_wall_ms=("wall_time_ms", "mean"),
            avg_cost_usd=("cost_usd", "mean"),
        )
        .reset_index()
    )
    # Cost per correct answer — the honest cross-mode comparison. A mode that
    # is cheap-per-call but rarely right is expensive per *point*; a mode that
    # costs more per call but answers what others can't can still win here.
    # ∞ (NaN) when a mode never succeeded, so it's clearly "no value at any
    # price" rather than a misleading 0.
    correct_per_group = summary["success_rate"] * summary["runs"]
    summary["cost_per_correct_usd"] = (
        (summary["avg_cost_usd"] * summary["runs"]) / correct_per_group
    ).where(correct_per_group > 0)
    return summary


def _seed(key: str, all_options: list[str], default: list[str]) -> None:
    """Seed a filter from URL query params (comma-separated) if present, else
    from ``default`` (intersected with what's available), else all options."""
    if key in st.session_state:
        return
    raw = st.query_params.get(key)
    if raw:
        wanted = [v for v in raw.split(",") if v in all_options]
        st.session_state[key] = wanted or all_options
    else:
        preferred = [v for v in default if v in all_options]
        st.session_state[key] = preferred or all_options


def main() -> None:
    st.set_page_config(page_title="SpAIder Benchmarks", layout="wide")
    st.title("SpAIder Benchmark Dashboard")
    st.caption(
        "**Vanilla** (model alone) vs **with-spaider** (model + SpAIder MCP) on "
        "the **hotpotqa** (public) and **acmeai** (private) corpora. "
        "Source: `benchmarks/runs/*.jsonl`. Rerun the same suite to track "
        "whether a change made it better or worse."
    )

    df = _load_runs(RUNS_DIR)
    if df.empty:
        st.warning(
            f"No runs found in {RUNS_DIR}. Run `make bench-clean` then the "
            "benchmark suite to populate (see benchmarks/README.md)."
        )
        return

    models = sorted(df["model"].dropna().unique())
    categories = sorted(df["category"].dropna().unique())
    modes = sorted(df["mode"].dropna().unique())
    _seed("selected_models", models, models)
    _seed("selected_categories", categories, DEFAULT_CATEGORIES)
    _seed("selected_modes", modes, DEFAULT_MODES)

    st.sidebar.header("Filters")
    selected_models = st.sidebar.multiselect("Model", options=models, key="selected_models")
    selected_categories = st.sidebar.multiselect("Corpus", options=categories, key="selected_categories")
    selected_modes = st.sidebar.multiselect("Mode", options=modes, key="selected_modes")

    st.query_params["selected_models"] = ",".join(selected_models)
    st.query_params["selected_categories"] = ",".join(selected_categories)
    st.query_params["selected_modes"] = ",".join(selected_modes)

    if selected_models:
        df = df[df["model"].isin(selected_models)]
    if selected_categories:
        df = df[df["category"].isin(selected_categories)]
    if selected_modes:
        df = df[df["mode"].isin(selected_modes)]
    if df.empty:
        st.warning("No runs match the selected filters.")
        return

    summary, scorecard, detail = st.tabs(
        ["Latest summary", "DeepEval scorecard", "Per-task detail"]
    )

    # ── Tab 1: Latest summary ──────────────────────────────────────────────
    with summary:
        st.subheader("Latest result — per (model, mode)")
        per_mode = _latest_summary(df, group_by_category=False)
        per_mode_cat = _latest_summary(df, group_by_category=True)
        fmt = {
            "success_rate": "{:.0%}", "avg_tokens_in": "{:,.0f}",
            "avg_tokens_out": "{:,.0f}", "avg_backend_tokens_in": "{:,.0f}",
            "avg_backend_tokens_out": "{:,.0f}", "avg_wall_ms": "{:,.0f}",
            "avg_cost_usd": "${:.4f}", "cost_per_correct_usd": "${:.4f}",
        }
        st.dataframe(per_mode.style.format(fmt), use_container_width=True, hide_index=True)
        st.markdown("**Per (model, mode, corpus)**")
        st.dataframe(per_mode_cat.style.format(fmt), use_container_width=True, hide_index=True)

        # with-spaider lift vs vanilla, per model.
        for (provider, model), block in per_mode.groupby(["provider", "model"]):
            present = set(block["mode"])
            if not {"vanilla", "with-spaider"}.issubset(present):
                continue
            v = float(block.loc[block["mode"] == "vanilla", "success_rate"].iloc[0])
            m = float(block.loc[block["mode"] == "with-spaider", "success_rate"].iloc[0])
            st.metric(f"`{provider}/{model}` — with-spaider lift vs vanilla", f"{(m - v) * 100:+.1f} pp")

    # ── Tab 2: DeepEval scorecard (public vs private) ──────────────────────
    with scorecard:
        st.subheader("DeepEval-style scorecard — F1 / EM / GEval")
        st.caption(
            "QA metrics on tasks with an `expected_output` gold answer. "
            "**F1** is token-overlap; **EM** normalised exact-match; **GEval** "
            "LLM-graded correctness in [0,1]. Read the `hotpotqa` (public) vs "
            "`acmeai` (private) rows side by side: the LLM already knows public "
            "trivia, so with-spaider barely moves GEval there; on private data "
            "it can't know, vanilla scores ~0 and with-spaider lifts every metric."
        )
        scored = df[df["f1_score"].notna() | df["exact_match"].notna() | df["geval_score"].notna()]
        if scored.empty:
            st.info(
                "No DeepEval-style scores yet. Run a sweep on a composite-oracle "
                "corpus (hotpotqa / acmeai)."
            )
        else:
            sc = (
                scored.groupby(["provider", "model", "mode", "category"])
                .agg(
                    runs=("run_id", "count"),
                    f1=("f1_score", "mean"),
                    em=("exact_match", "mean"),
                    geval=("geval_score", "mean"),
                )
                .reset_index()
                .assign(stack=lambda d: d["provider"] + "/" + d["model"])
                [["stack", "mode", "category", "runs", "f1", "em", "geval"]]
                .sort_values(["category", "mode"])
            )
            st.dataframe(
                sc.style.format({"f1": "{:.3f}", "em": "{:.3f}", "geval": "{:.3f}"}),
                use_container_width=True, hide_index=True,
            )

    # ── Tab 3: Per-task detail ─────────────────────────────────────────────
    with detail:
        st.subheader("Per-task drill-down")
        task_ids = sorted(df["task_id"].unique())
        if not task_ids:
            st.info("No tasks recorded.")
            return
        chosen = st.selectbox("Task", task_ids)
        task_df = df[df["task_id"] == chosen].sort_values("started_at", ascending=False)
        for (provider, model), block in task_df.groupby(["provider", "model"]):
            st.markdown(f"### `{provider}/{model}`")
            for mode in DEFAULT_MODES:
                mode_df = block[block["mode"] == mode]
                if mode_df.empty:
                    continue
                row = mode_df.iloc[0]
                cost_str = f"${row['cost_usd']:.4f}" if row["cost_usd"] > 0 else "free"
                bt_in = int(row.get("backend_tokens_in", 0) or 0)
                bt_out = int(row.get("backend_tokens_out", 0) or 0)
                backend_str = (
                    f" backend={bt_in}/{bt_out}" if (bt_in or bt_out) else ""
                )
                with st.expander(
                    f"{mode} — {'✓ pass' if row['success'] else '✗ fail'} — "
                    f"{row['wall_time_ms']:.0f}ms — in={row['tokens_in']} "
                    f"out={row['tokens_out']}{backend_str} "
                    f"tools={row['tool_calls']} cost={cost_str}",
                    expanded=True,
                ):
                    if row.get("error"):
                        st.error(row["error"])
                    if row.get("judge_rationale"):
                        st.markdown(f"**Judge rationale:** {row['judge_rationale']}")
                    st.markdown("**Final answer**")
                    st.code(row["final_text"] or "(empty)", language="markdown")
                    reasoning = row.get("reasoning_text") or ""
                    if reasoning:
                        with st.expander("Reasoning trace (chain-of-thought)"):
                            st.code(reasoning, language="markdown")


if __name__ == "__main__":
    main()
