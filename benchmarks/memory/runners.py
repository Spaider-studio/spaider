"""
Runners for memory benchmarks in the style of LongMemEval, LoCoMo and MemoryAgentBench.

Each loader turns a dataset file into conversations plus questions. The runner
feeds a memory backend, asks the questions and scores with exact match and token
F1, which need no model. These runners have only been exercised on small synthetic
fixtures with a fake backend. No result on the real datasets exists yet.

    python -m benchmarks.memory.runners locomo data.json --url http://localhost:8000 \\
        --api-key ss-... --agent kb-id --output report.json
"""
from __future__ import annotations

import argparse
import json
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Optional, Protocol

_ARTICLES = re.compile(r"\b(a|an|the)\b")
_PUNCTUATION = re.compile(r"[^\w\s]")


@dataclass
class Turn:
    text: str
    session: str


@dataclass
class Question:
    question: str
    answer: str
    category: str = "general"


@dataclass
class Case:
    case_id: str
    turns: list[Turn]
    questions: list[Question]


@dataclass
class Report:
    benchmark: str
    questions: int
    exact_match: float
    f1: float
    by_category: dict[str, float] = field(default_factory=dict)
    note: str = "Scores use exact match and token F1 only."


class Backend(Protocol):
    def reset(self, case_id: str) -> None: ...

    def add(self, turn: Turn) -> None: ...

    def answer(self, question: str) -> str: ...


def normalise(text: str) -> list[str]:
    return _ARTICLES.sub(" ", _PUNCTUATION.sub(" ", text.lower())).split()


def exact_match(prediction: str, gold: str) -> float:
    return float(normalise(prediction) == normalise(gold))


def token_f1(prediction: str, gold: str) -> float:
    predicted, expected = normalise(prediction), normalise(gold)
    if not predicted or not expected:
        return float(predicted == expected)
    remaining = list(expected)
    common = 0
    for token in predicted:
        if token in remaining:
            remaining.remove(token)
            common += 1
    if not common:
        return 0.0
    precision, recall = common / len(predicted), common / len(expected)
    return 2 * precision * recall / (precision + recall)


def load_longmemeval(data: list[dict]) -> list[Case]:
    cases = []
    for item in data:
        turns = [
            Turn(f"{turn['role']}: {turn['content']}", str(session_id))
            for session_id, session in zip(item["haystack_session_ids"], item["haystack_sessions"])
            for turn in session
        ]
        question = Question(item["question"], str(item["answer"]), item.get("question_type", "general"))
        cases.append(Case(item["question_id"], turns, [question]))
    return cases


def load_locomo(data: list[dict]) -> list[Case]:
    cases = []
    for index, item in enumerate(data):
        conversation = item["conversation"]
        sessions = sorted(
            (key for key in conversation if re.fullmatch(r"session_\d+", key)),
            key=lambda key: int(key.split("_")[1]),
        )
        turns = [
            Turn(f"{turn['speaker']}: {turn['text']}", session) for session in sessions for turn in conversation[session]
        ]
        questions = [
            Question(qa["question"], str(qa["answer"]), f"category_{qa.get('category', 0)}")
            for qa in item["qa"]
            if "answer" in qa
        ]
        cases.append(Case(str(item.get("sample_id", index)), turns, questions))
    return cases


def load_memory_agent_bench(data: list[dict]) -> list[Case]:
    cases = []
    for index, item in enumerate(data):
        turns = [Turn(chunk, f"chunk_{position}") for position, chunk in enumerate(item["context_chunks"])]
        questions = [Question(q, str(a), item.get("task", "general")) for q, a in zip(item["questions"], item["answers"])]
        cases.append(Case(str(item.get("id", index)), turns, questions))
    return cases


LOADERS: dict[str, Callable[[list[dict]], list[Case]]] = {
    "longmemeval": load_longmemeval,
    "locomo": load_locomo,
    "memoryagentbench": load_memory_agent_bench,
}


def run(benchmark: str, cases: Iterable[Case], backend: Backend, *, limit: Optional[int] = None) -> Report:
    match_scores, f1_scores = [], []
    categories: dict[str, list[float]] = {}
    for position, case in enumerate(cases):
        if limit is not None and position >= limit:
            break
        backend.reset(case.case_id)
        for turn in case.turns:
            backend.add(turn)
        for question in case.questions:
            prediction = backend.answer(question.question)
            match_scores.append(exact_match(prediction, question.answer))
            score = token_f1(prediction, question.answer)
            f1_scores.append(score)
            categories.setdefault(question.category, []).append(score)
    count = len(f1_scores)
    return Report(
        benchmark=benchmark,
        questions=count,
        exact_match=round(sum(match_scores) / count, 4) if count else 0.0,
        f1=round(sum(f1_scores) / count, 4) if count else 0.0,
        by_category={name: round(sum(values) / len(values), 4) for name, values in sorted(categories.items())},
    )


class SpaiderBackend:
    """Against a throwaway KB only: it writes every turn."""

    def __init__(self, url: str, api_key: str, agent_id: str) -> None:
        from spaider import Spaider

        self._client = Spaider(api_key=api_key, agent_id=agent_id, base_url=url)

    def reset(self, case_id: str) -> None:
        self._session = case_id

    def add(self, turn: Turn) -> None:
        self._client.remember(turn.text, session_id=f"{self._session}:{turn.session}")

    def answer(self, question: str) -> str:
        return self._client.query(question).answer


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("benchmark", choices=sorted(LOADERS))
    parser.add_argument("dataset", type=Path)
    parser.add_argument("--url", required=True)
    parser.add_argument("--api-key", required=True)
    parser.add_argument("--agent", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--output", type=Path)
    args = parser.parse_args()
    cases = LOADERS[args.benchmark](json.loads(args.dataset.read_text(encoding="utf-8")))
    report = run(args.benchmark, cases, SpaiderBackend(args.url, args.api_key, args.agent), limit=args.limit)
    text = json.dumps(asdict(report), indent=2)
    if args.output:
        args.output.write_text(text, encoding="utf-8")
    print(text)


if __name__ == "__main__":
    main()
