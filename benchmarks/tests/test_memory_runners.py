"""Runners against synthetic fixtures and a fake backend. Says nothing about real scores."""
from __future__ import annotations

import pytest

from benchmarks.memory.runners import (
    LOADERS,
    Turn,
    exact_match,
    load_locomo,
    load_longmemeval,
    load_memory_agent_bench,
    run,
    token_f1,
)


class Remembers:
    """Answers by finding the last stored turn that shares a word with the question."""

    def __init__(self):
        self.turns = []
        self.resets = []

    def reset(self, case_id):
        self.resets.append(case_id)
        self.turns = []

    def add(self, turn: Turn):
        self.turns.append(turn.text)

    def answer(self, question):
        words = set(question.lower().rstrip("?").split())
        for text in reversed(self.turns):
            if words & set(text.lower().split()):
                return text.split(": ")[-1]
        return ""


LONGMEM = [
    {
        "question_id": "q1",
        "question": "Which city?",
        "answer": "Berlin",
        "question_type": "single-session",
        "haystack_session_ids": ["s1"],
        "haystack_sessions": [[{"role": "user", "content": "Which city? Berlin"}]],
    }
]
LOCOMO = [
    {
        "sample_id": "c1",
        "conversation": {
            "speaker_a": "A",
            "session_2": [{"speaker": "A", "text": "late note"}],
            "session_1": [{"speaker": "A", "text": "early note"}],
            "session_1_date_time": "ignored",
        },
        "qa": [{"question": "q", "answer": "x", "category": 2}, {"question": "adversarial", "adversarial_answer": "y"}],
    }
]
AGENT_BENCH = [{"id": "m1", "context_chunks": ["alpha is one"], "questions": ["alpha?"], "answers": ["alpha is one"], "task": "recall"}]


@pytest.mark.parametrize(
    ("prediction", "gold", "expected"),
    [("The Berlin.", "berlin", 1.0), ("Paris", "Berlin", 0.0)],
)
def test_exact_match(prediction, gold, expected):
    assert exact_match(prediction, gold) == expected


def test_token_f1():
    assert token_f1("Berlin", "Berlin") == 1.0
    assert token_f1("", "Berlin") == 0.0
    assert token_f1("", "") == 1.0
    assert 0 < token_f1("Berlin Germany", "Berlin") < 1


def test_longmemeval_loader():
    case = load_longmemeval(LONGMEM)[0]
    assert case.case_id == "q1" and case.turns[0].session == "s1" and case.questions[0].category == "single-session"


def test_locomo_loader_orders_sessions_and_skips_unanswerable():
    case = load_locomo(LOCOMO)[0]
    assert [turn.session for turn in case.turns] == ["session_1", "session_2"]
    assert [q.answer for q in case.questions] == ["x"] and case.questions[0].category == "category_2"


def test_memory_agent_bench_loader():
    case = load_memory_agent_bench(AGENT_BENCH)[0]
    assert case.turns[0].text == "alpha is one" and case.questions[0].category == "recall"


@pytest.mark.parametrize(("name", "data"), [("longmemeval", LONGMEM), ("memoryagentbench", AGENT_BENCH)])
def test_runner_scores_a_backend_that_remembers(name, data):
    report = run(name, LOADERS[name](data), Remembers())
    assert report.questions == 1 and report.f1 == 1.0 and report.exact_match == 1.0


def test_each_case_starts_clean():
    backend = Remembers()
    cases = load_memory_agent_bench(AGENT_BENCH * 2)
    run("x", cases, backend)
    assert len(backend.resets) == 2 and len(backend.turns) == 1


def test_limit():
    assert run("x", load_memory_agent_bench(AGENT_BENCH * 3), Remembers(), limit=1).questions == 1


def test_no_questions_is_not_a_crash():
    assert run("x", [], Remembers()).f1 == 0.0
