"""
Generate a deterministic distractor "haystack" corpus from the public
HotpotQA dev set (distractor variant).

Why this exists
---------------
The 24-question HotpotQA suite in ``benchmarks/tasks/hotpotqa/`` is a
*parity* benchmark: 48 supporting paragraphs (~4 KB total) — small
enough to fit trivially in any 128K-context model. At that scale
``vanilla-context`` mode pastes the whole corpus into the prompt and
wins by structural advantage. SpAIder's actual moat — retrieval from
corpora too large to fit in context — is not exercised.

This generator embeds the same 48 supporting paragraphs in a much
larger pool of HotpotQA distractor paragraphs, producing a corpus
where:

  * ``vanilla-context`` mode can no longer fit the whole corpus and
    must either fail (token-limit refusal) or degrade as haystack noise
    crowds out the gold paragraphs.
  * ``with-spaider`` mode retrieves the relevant paragraphs from the
    graph at roughly the same cost as the parity benchmark — the
    promised cost-and-feasibility moat.

Determinism
-----------
The HotpotQA dev set is sorted by the deterministic ``_id`` field and
deduplicated by ``(question_id, title)``. ``random.Random(seed=42)``
shuffles the deduplicated list into a stable order. Re-running with
the same source JSON produces a byte-identical YAML/TXT output.

The output is checked into the repo so consumers do not need to run
this generator to use the haystack benchmark.

Source data
-----------
Public HotpotQA dev set (distractor variant), license CC-BY-SA 4.0.
Default URL: ``http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json``

Run
---
    # First time — downloads ~47 MB to benchmarks/corpus/.cache/ (gitignored)
    benchmarks/.venv/bin/python -m benchmarks.generate_hotpotqa_haystack

    # Already have the JSON locally:
    benchmarks/.venv/bin/python -m benchmarks.generate_hotpotqa_haystack \\
        --source /path/to/hotpot_dev_distractor_v1.json

    # Custom token target (default 50000):
    benchmarks/.venv/bin/python -m benchmarks.generate_hotpotqa_haystack \\
        --target-tokens 200000
"""
from __future__ import annotations

import argparse
import hashlib
import json
import random
import sys
import urllib.request
from pathlib import Path

import yaml

CORPUS_DIR = Path(__file__).parent / "corpus"
CACHE_DIR = CORPUS_DIR / ".cache"
GOLD_CORPUS = CORPUS_DIR / "hotpotqa_24.yaml"
DEFAULT_SOURCE_URL = (
    "http://curtis.ml.cmu.edu/datasets/hotpot/hotpot_dev_distractor_v1.json"
)
DEFAULT_OUTPUT_STEM = "hotpotqa_haystack"

DEFAULT_TARGET_TOKENS = 50_000
RANDOM_SEED = 42

# Heuristic: HotpotQA paragraphs are tokenized roughly 1 token per 0.75 word.
# Slightly conservative so we hit-or-exceed the target.
WORDS_PER_TOKEN = 0.75


def _gold_question_ids() -> set[str]:
    """Return the HotpotQA question IDs whose paragraphs are already in the
    gold corpus (``hotpotqa_24.yaml``). Their distractors must be excluded
    from the haystack — otherwise we'd be leaking ground truth."""
    raw = yaml.safe_load(GOLD_CORPUS.read_text(encoding="utf-8"))
    ids: set[str] = set()
    for fact in raw.get("facts", []):
        # Sources look like: hotpotqa:5a8b57f25542995d1e6f1371#Scott Derrickson
        src = fact.get("source", "")
        if src.startswith("hotpotqa:"):
            ids.add(src.split(":", 1)[1].split("#", 1)[0])
    return ids


def _gold_paragraph_keys() -> set[tuple[str, str]]:
    """Return ``(question_id, paragraph_title)`` pairs already in the gold
    corpus, so we don't double-ingest them when sampling distractors."""
    raw = yaml.safe_load(GOLD_CORPUS.read_text(encoding="utf-8"))
    keys: set[tuple[str, str]] = set()
    for fact in raw.get("facts", []):
        src = fact.get("source", "")
        if src.startswith("hotpotqa:") and "#" in src:
            qid, title = src.split(":", 1)[1].split("#", 1)
            keys.add((qid, title))
    return keys


def _ensure_source(source: Path) -> None:
    """Download the dev distractor JSON to the cache dir if missing."""
    if source.exists():
        return
    source.parent.mkdir(parents=True, exist_ok=True)
    print(
        f"downloading HotpotQA dev distractor set (~47 MB) to {source}…",
        file=sys.stderr,
    )
    urllib.request.urlretrieve(DEFAULT_SOURCE_URL, source)
    print(f"  done. SHA256={hashlib.sha256(source.read_bytes()).hexdigest()[:16]}…", file=sys.stderr)


def _enumerate_paragraphs(source_json: list[dict]) -> list[dict]:
    """Walk every distractor paragraph in the dev set and emit a flat list of
    ``{question_id, title, text}`` records, deduplicated by ``(qid, title)``.

    Each HotpotQA dev question has a ``context`` field of 10 entries, each
    ``[title, [sentence_list]]``. We join the sentences into a single
    paragraph text. Gold paragraphs are *included* here; the caller
    filters them.
    """
    out: list[dict] = []
    seen: set[tuple[str, str]] = set()
    for q in source_json:
        qid = q.get("_id") or ""
        for entry in q.get("context", []):
            if not entry or len(entry) < 2:
                continue
            title, sentences = entry[0], entry[1]
            if not isinstance(title, str) or not isinstance(sentences, list):
                continue
            key = (qid, title)
            if key in seen:
                continue
            seen.add(key)
            text = "".join(s for s in sentences if isinstance(s, str)).strip()
            if not text:
                continue
            out.append({"question_id": qid, "title": title, "text": text})
    # Sort by (qid, title) for total order. Then a seeded shuffle gives a
    # stable shuffle — same input → same output, every time.
    out.sort(key=lambda r: (r["question_id"], r["title"]))
    return out


def _approx_tokens(text: str) -> int:
    return int(len(text.split()) / WORDS_PER_TOKEN)


def _build_corpus(
    paragraphs: list[dict],
    *,
    target_tokens: int,
    excluded_keys: set[tuple[str, str]],
) -> list[dict]:
    """Sample paragraphs in deterministic shuffled order until we hit the
    token target, skipping anything in ``excluded_keys``."""
    rng = random.Random(RANDOM_SEED)
    shuffled = paragraphs[:]
    rng.shuffle(shuffled)

    chosen: list[dict] = []
    total_tokens = 0
    for p in shuffled:
        if (p["question_id"], p["title"]) in excluded_keys:
            continue
        toks = _approx_tokens(p["text"])
        if toks == 0:
            continue
        chosen.append(p)
        total_tokens += toks
        if total_tokens >= target_tokens:
            break
    return chosen


def _write_outputs(
    distractors: list[dict],
    gold_facts: list[dict],
    *,
    output_stem: str,
) -> None:
    """Write the YAML (gold + distractors) and a flat TXT for vanilla-context
    mode probes that want to check what 'too big to fit' actually looks like.
    ``output_stem`` controls the output filenames so multiple scale levels
    can coexist on disk."""
    out_yaml = CORPUS_DIR / f"{output_stem}.yaml"
    out_txt = CORPUS_DIR / f"{output_stem}.txt"

    out_facts: list[dict] = []
    # Gold paragraphs first — they're the *answer* to the questions and must
    # always be retrievable. Putting them first also means callers using
    # `--limit N` for small scales still keep the ground truth.
    for fact in gold_facts:
        out_facts.append(fact)
    for d in distractors:
        out_facts.append({
            "date": "2026-04-30",
            "type": "WIKIPEDIA",
            "text": f"[Wikipedia: {d['title']}] {d['text']}",
            "source": f"hotpotqa-distractor:{d['question_id']}#{d['title']}",
        })

    yaml_doc = {
        "company": "HotpotQA-Haystack",
        "window": {"start": "2026-04-30", "days": 1},
        "facts": out_facts,
    }
    out_yaml.write_text(yaml.safe_dump(yaml_doc, sort_keys=False, allow_unicode=True), encoding="utf-8")

    # Flat text dump for cost-comparison probes.
    out_txt.write_text(
        "\n\n".join(f["text"] for f in out_facts),
        encoding="utf-8",
    )

    yaml_sha = hashlib.sha256(out_yaml.read_bytes()).hexdigest()[:16]
    txt_sha = hashlib.sha256(out_txt.read_bytes()).hexdigest()[:16]
    txt_tokens = _approx_tokens(out_txt.read_text(encoding="utf-8"))
    print(
        f"wrote {out_yaml.relative_to(Path.cwd())}  "
        f"({len(out_facts)} facts, ~{txt_tokens} tokens)  sha256={yaml_sha}…",
        file=sys.stderr,
    )
    print(f"wrote {out_txt.relative_to(Path.cwd())}  sha256={txt_sha}…", file=sys.stderr)


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__.split("\n")[1])
    p.add_argument(
        "--source", type=Path,
        default=CACHE_DIR / "hotpot_dev_distractor_v1.json",
        help="path to the HotpotQA dev distractor JSON (downloaded if absent)",
    )
    p.add_argument(
        "--target-tokens", type=int, default=DEFAULT_TARGET_TOKENS,
        help=f"approximate haystack size in tokens (default {DEFAULT_TARGET_TOKENS})",
    )
    p.add_argument(
        "--output-stem", default=None,
        help=(
            "filename stem for outputs (writes to corpus/<stem>.yaml + .txt). "
            f"Default: '{DEFAULT_OUTPUT_STEM}' for the canonical 50K-token "
            "haystack, otherwise '{DEFAULT_OUTPUT_STEM}_<target_tokens>k' for "
            "larger scales so they coexist on disk without overwriting."
        ),
    )
    args = p.parse_args()
    if args.output_stem is None:
        if args.target_tokens == DEFAULT_TARGET_TOKENS:
            args.output_stem = DEFAULT_OUTPUT_STEM
        else:
            args.output_stem = f"{DEFAULT_OUTPUT_STEM}_{args.target_tokens // 1000}k"

    _ensure_source(args.source)
    print(f"loading dev set from {args.source}…", file=sys.stderr)
    source_data = json.loads(args.source.read_text(encoding="utf-8"))
    para_count = sum(len(q.get("context", [])) for q in source_data)
    print(f"  {len(source_data)} questions, {para_count} paragraphs total", file=sys.stderr)

    paragraphs = _enumerate_paragraphs(source_data)
    excluded = _gold_paragraph_keys()
    print(
        f"  {len(paragraphs)} unique paragraphs after dedup; "
        f"{len(excluded)} gold paragraphs excluded.",
        file=sys.stderr,
    )

    distractors = _build_corpus(
        paragraphs,
        target_tokens=args.target_tokens,
        excluded_keys=excluded,
    )
    chosen_tokens = sum(_approx_tokens(d["text"]) for d in distractors)
    print(
        f"  selected {len(distractors)} distractor paragraphs "
        f"(~{chosen_tokens} tokens, target {args.target_tokens}).",
        file=sys.stderr,
    )

    gold_facts = yaml.safe_load(GOLD_CORPUS.read_text(encoding="utf-8")).get("facts", [])
    _write_outputs(distractors, gold_facts, output_stem=args.output_stem)
    print("done.", file=sys.stderr)
    return 0


if __name__ == "__main__":
    sys.exit(main())
