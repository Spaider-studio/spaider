"""
One-shot restructure of HotpotQA task YAMLs.

The original `prompt` field bundles two distinct things:

    Were Scott Derrickson and Ed Wood of the same nationality?

    Answer in as few words as possible. Just the answer, no explanation.

The first paragraph is the *question*. The second paragraph is a *format
hint* about how to shape the final answer. Bundling them together caused
the runner LLM to read "answer in as few words as possible" *before* it
considered tool use, treat the question as something to answer
immediately, and skip retrieval entirely. 6 of 7 chronic-fail tasks had
``tool_calls = 0`` for exactly this reason.

This script:

1. Walks ``benchmarks/tasks/hotpotqa/*.yaml``.
2. For each task whose `prompt` matches the (question + format hint)
   pattern, splits the trailing format hint into the new `format_hint`
   field.
3. Trims `prompt` down to the question alone.
4. Writes the result back in place. Idempotent — files already in the
   new shape are left alone.

Run once after merging. The runner reads `format_hint` and
appends it to the conversation *after* the first tool result, where it
can shape the answer without suppressing the initial spaider.query call.

Usage
-----
    benchmarks/.venv/bin/python -m benchmarks.scripts.restructure_hotpotqa_prompts
"""
from __future__ import annotations

import sys
from pathlib import Path

import yaml

# Recognised format-hint prefixes — exact-match (case-insensitive after
# .strip()) against the *last* paragraph of the prompt. Conservative on
# purpose; anything we don't recognise is left untouched and the YAML
# is reported as 'unchanged' so the user can inspect manually.
_KNOWN_FORMAT_HINTS: list[str] = [
    "answer in as few words as possible. just the answer, no explanation.",
    "answer in as few words as possible.",
    "just the answer, no explanation.",
]


def _split_question_and_hint(prompt: str) -> tuple[str, str | None]:
    """Return (question, format_hint_or_None). If the trailing paragraph
    isn't one of the known hints, return (prompt_unchanged, None)."""
    paragraphs = [p.strip() for p in prompt.strip().split("\n\n") if p.strip()]
    if len(paragraphs) < 2:
        return prompt.strip(), None
    last = paragraphs[-1].lower()
    if last not in _KNOWN_FORMAT_HINTS:
        return prompt.strip(), None
    question = "\n\n".join(paragraphs[:-1]).strip()
    return question, paragraphs[-1].strip()


def _restructure_yaml(path: Path) -> tuple[bool, str]:
    """Return (changed, message)."""
    raw = path.read_text(encoding="utf-8")
    data = yaml.safe_load(raw)
    if not isinstance(data, dict) or "prompt" not in data:
        return False, "no prompt field"
    if "format_hint" in data:
        return False, "already has format_hint"

    question, hint = _split_question_and_hint(data["prompt"])
    if hint is None:
        return False, "no recognised format-hint suffix"

    # Build the new dict with `format_hint` placed right after `prompt`.
    out: dict = {}
    for k, v in data.items():
        if k == "prompt":
            out[k] = question
            out["format_hint"] = hint
        else:
            out[k] = v

    path.write_text(
        yaml.safe_dump(out, sort_keys=False, allow_unicode=True),
        encoding="utf-8",
    )
    return True, f"split → format_hint={hint!r}"


def main() -> int:
    repo = Path(__file__).resolve().parent.parent.parent
    tasks_dir = repo / "benchmarks" / "tasks" / "hotpotqa"
    if not tasks_dir.is_dir():
        print(f"error: {tasks_dir} not found", file=sys.stderr)
        return 2

    files = sorted(tasks_dir.glob("*.yaml"))
    if not files:
        print(f"no *.yaml files in {tasks_dir}", file=sys.stderr)
        return 1

    print(f"restructuring {len(files)} HotpotQA YAML(s) in {tasks_dir}…")
    changed_n = 0
    for f in files:
        changed, msg = _restructure_yaml(f)
        flag = "RW" if changed else "--"
        print(f"  [{flag}] {f.name:<60} {msg}")
        changed_n += int(changed)
    print(f"\n{changed_n} of {len(files)} files updated.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
