"""Re-judge already-generated answers with an independent LLM judge.

The runner's native ``geval_score`` reuses the *run* model as its own judge
(self-consistency bias caveat). This re-scores the same ``final_text`` answers
with a stronger/independent judge (e.g. gpt-4o) and writes a
``geval__<model>`` field per row, so the community scorecard can show both
judges side by side.

Generation is NOT re-run — only the (cheap) judging pass. Question + gold are
joined back from the task YAMLs by ``task_id``.

Usage:
    LLM_API_KEY=sk-... python -m benchmarks.rejudge \
        --runs benchmarks/runs --tasks benchmarks/tasks/hotpotqa --judge-model gpt-4o
"""
from __future__ import annotations

import argparse
import asyncio
import glob
import json
import os
import re

import yaml

# Identical rubric to runner._judge_geval so the only variable is the judge model.
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


def _load_tasks(tasks_dir: str) -> dict[str, dict[str, str]]:
    m: dict[str, dict[str, str]] = {}
    for fp in glob.glob(os.path.join(tasks_dir, "*.yaml")):
        t = yaml.safe_load(open(fp))
        if t and t.get("id"):
            m[t["id"]] = {"q": t.get("prompt", ""), "exp": str(t.get("expected_output", ""))}
    return m


async def _judge(model, question, expected, answer, api_key, base_url):
    from litellm import acompletion
    if not (answer or "").strip():
        return 0.0, "(empty answer)"
    prompt = _GEVAL_PROMPT_TEMPLATE.format(question=question, expected=expected, answer=answer)
    kw = {"model": model, "messages": [{"role": "user", "content": prompt}],
          "temperature": 0.0, "max_tokens": 400}
    if api_key:
        kw["api_key"] = api_key
    if base_url:
        kw["api_base"] = base_url
    try:
        resp = await acompletion(**kw)
        raw = (resp.choices[0].message.content or "").strip()
        s, e = raw.find("{"), raw.rfind("}")
        if s == -1 or e < s:
            return 0.0, f"(judge non-JSON: {raw[:80]!r})"
        p = json.loads(raw[s:e + 1])
        return max(0.0, min(1.0, float(p.get("score", 0.0)))), str(p.get("rationale", ""))[:200]
    except Exception as ex:  # noqa: BLE001
        return 0.0, f"(judge error: {type(ex).__name__}: {ex})"


async def _run(args):
    tasks = _load_tasks(args.tasks)
    field = "geval__" + re.sub(r"[^A-Za-z0-9]+", "-", args.judge_model)
    api_key = os.environ.get("LLM_API_KEY") or os.environ.get("OPENAI_API_KEY")
    base_url = os.environ.get("LLM_BASE_URL")
    sem = asyncio.Semaphore(args.concurrency)
    total = 0
    for path in glob.glob(os.path.join(args.runs, "*.jsonl")):
        rows = [json.loads(ln) for ln in open(path) if ln.strip()]

        async def do(r):
            t = tasks.get(r.get("task_id"))
            if not t:
                return
            async with sem:
                score, rat = await _judge(
                    args.judge_model, t["q"], t["exp"], r.get("final_text", ""), api_key, base_url)
            r[field] = score
            r[field + "__rationale"] = rat

        await asyncio.gather(*[do(r) for r in rows])
        with open(path, "w") as fh:
            for r in rows:
                fh.write(json.dumps(r) + "\n")
        total += len(rows)
        print(f"{os.path.basename(path)}: re-judged {len(rows)} rows -> {field}")
    print(f"done: {total} rows judged by {args.judge_model}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--runs", default="benchmarks/runs")
    ap.add_argument("--tasks", default="benchmarks/tasks/hotpotqa")
    ap.add_argument("--judge-model", default="gpt-4o")
    ap.add_argument("--concurrency", type=int, default=8)
    asyncio.run(_run(ap.parse_args()))


if __name__ == "__main__":
    main()
