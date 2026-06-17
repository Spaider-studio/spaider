"""Unit tests for the benchmark runner — pure-Python, no Anthropic key needed."""
from __future__ import annotations

from pathlib import Path

import pytest

from benchmarks.runner import (
    LLMConfig,
    Task,
    _compute_exact_match,
    _compute_f1,
    _compute_retrieval_hits,
    _compute_rouge_l,
    _feedback_url_from_mcp,
    _judge_substring,
    _normalize_qa_text,
    _parse_backend_tokens_trailer,
    _parse_node_ids_trailer,
    _runs_path,
    load_tasks,
)


@pytest.fixture
def repo_tasks_dir() -> Path:
    return Path(__file__).resolve().parent.parent / "tasks"


def test_load_tasks_directory(repo_tasks_dir: Path) -> None:
    tasks = load_tasks(repo_tasks_dir)
    assert len(tasks) >= 3, "expected the shipped v1 task suite to have >= 3 tasks"
    ids = {t.id for t in tasks}
    assert "01_merge_order" in ids
    # Every shipped substring task must declare a non-empty prompt AND
    # at least one judging hook — otherwise it cannot fail.
    for t in tasks:
        assert t.prompt.strip(), f"task {t.id} has empty prompt"
        if t.oracle_kind == "substring":
            assert t.expected_substring or t.expected_all, (
                f"task {t.id} has no expected_substring or expected_all — "
                "it would always pass"
            )
        elif t.oracle_kind == "llm_judge":
            assert (t.oracle_rubric or "").strip(), (
                f"task {t.id} has llm_judge oracle but empty rubric"
            )


def test_load_tasks_single_file(repo_tasks_dir: Path) -> None:
    one = sorted(repo_tasks_dir.glob("*.yaml"))[0]
    tasks = load_tasks(one)
    assert len(tasks) == 1
    assert tasks[0].id == one.stem


def test_load_tasks_missing(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_tasks(tmp_path / "does-not-exist")


def test_load_tasks_empty_dir(tmp_path: Path) -> None:
    with pytest.raises(FileNotFoundError):
        load_tasks(tmp_path)


def _task(**kwargs) -> Task:
    base = dict(id="t", title="T", prompt="P")
    base.update(kwargs)
    return Task(**base)  # type: ignore[arg-type]


def test_judge_substring_substring_pass() -> None:
    assert _judge_substring("the answer is FORTY-TWO", _task(expected_substring="forty-two"))


def test_judge_substring_substring_fail() -> None:
    assert not _judge_substring("nope", _task(expected_substring="forty-two"))


def test_judge_substring_expected_all_requires_every_needle() -> None:
    task = _task(expected_all=["alpha", "beta"])
    assert _judge_substring("alpha and beta both here", task)
    assert not _judge_substring("only alpha", task)


def test_judge_substring_empty_text_is_failure() -> None:
    assert not _judge_substring("", _task(expected_substring="anything"))
    assert not _judge_substring("   ", _task(expected_all=["x"]))


def test_judge_substring_combines_substring_and_expected_all() -> None:
    task = _task(expected_substring="root", expected_all=["leaf"])
    assert _judge_substring("root and leaf", task)
    assert not _judge_substring("root only", task)
    assert not _judge_substring("leaf only", task)


def test_runs_path_is_date_stamped(tmp_path: Path) -> None:
    p = _runs_path(tmp_path, "anthropic", "claude-haiku-4-5")
    assert p.parent == tmp_path
    assert p.suffix == ".jsonl"
    assert "anthropic" in p.name
    assert "claude-haiku-4-5" in p.name
    # The file is created lazily by _append_record, not by _runs_path itself.
    assert p.parent.exists()


def test_runs_path_sanitises_slashes_and_colons(tmp_path: Path) -> None:
    p = _runs_path(tmp_path, "ollama", "library/llama3.2:3b")
    assert "/" not in p.name
    assert ":" not in p.name


def test_litellm_model_string_ollama_uses_chat_endpoint() -> None:
    cfg = LLMConfig(provider="ollama", model="llama3.2:3b", api_key=None, base_url=None)
    # ollama_chat/ prefix is required for tool calling — the bare ollama/
    # prefix routes to the legacy completion API which does not support tools.
    assert cfg.litellm_model == "ollama_chat/llama3.2:3b"


def test_litellm_model_string_openai_is_bare() -> None:
    cfg = LLMConfig(provider="openai", model="gpt-4o-mini", api_key="sk-x", base_url=None)
    assert cfg.litellm_model == "gpt-4o-mini"


def test_litellm_model_string_anthropic_is_prefixed() -> None:
    cfg = LLMConfig(provider="anthropic", model="claude-haiku-4-5", api_key="sk-x", base_url=None)
    assert cfg.litellm_model == "anthropic/claude-haiku-4-5"


def test_compounding_brain_tasks_load_with_llm_judge() -> None:
    """The 10 cb_*.yaml tasks must all use the llm_judge oracle and ship a rubric."""
    cb_dir = Path(__file__).resolve().parent.parent / "tasks" / "compounding_brain"
    assert cb_dir.is_dir(), f"expected {cb_dir} to exist"
    tasks = load_tasks(cb_dir)
    assert len(tasks) == 10, f"expected 10 compounding-brain tasks, got {len(tasks)}"
    for t in tasks:
        assert t.oracle_kind == "llm_judge", f"{t.id} should be llm_judge"
        assert (t.oracle_rubric or "").strip(), f"{t.id} has empty rubric"
        assert t.category == "compounding_brain", f"{t.id} category mismatch"


def test_parse_node_ids_trailer_extracts_ids() -> None:
    text = (
        "Answer:\nfoo\n\n"
        "Confidence: 0.50  |  Iterations: 1  |  From cache: False\n\n"
        "Top supporting entities: Stark(ORG), Tony(PERSON)\n\n"
        "Node IDs (for feedback): abc-1, def-2, ghi-3"
    )
    assert _parse_node_ids_trailer(text) == ["abc-1", "def-2", "ghi-3"]


def test_parse_node_ids_trailer_returns_empty_when_absent() -> None:
    assert _parse_node_ids_trailer("Answer: hello\n\nNo trailer here.") == []
    assert _parse_node_ids_trailer("") == []


def test_parse_node_ids_trailer_strips_whitespace_and_drops_empties() -> None:
    text = "Node IDs (for feedback): abc-1 ,  def-2,, ghi-3 "
    assert _parse_node_ids_trailer(text) == ["abc-1", "def-2", "ghi-3"]


def test_parse_backend_tokens_trailer_extracts_counts() -> None:
    text = (
        "Answer:\nfoo\n\n"
        "Node IDs (for feedback): abc-1\n\n"
        "Backend tokens: in=1234 out=56"
    )
    assert _parse_backend_tokens_trailer(text) == (1234, 56)


def test_parse_backend_tokens_trailer_absent_returns_zeros() -> None:
    # Older backend or a non-query tool result has no trailer.
    assert _parse_backend_tokens_trailer("Answer: hi\n\nNo trailer.") == (0, 0)
    assert _parse_backend_tokens_trailer("") == (0, 0)


def test_parse_backend_tokens_trailer_handles_zero_and_order() -> None:
    assert _parse_backend_tokens_trailer("Backend tokens: out=9 in=0") == (0, 9)


def test_run_record_total_tokens_sum_agent_and_backend() -> None:
    from benchmarks.runner import RunRecord

    rec = RunRecord(
        run_id="r", task_id="t", task_title="T", category="c", mode="with-spaider",
        provider="openai", model="gpt-4o", started_at="2026-01-01T00:00:00+00:00",
        wall_time_ms=1.0, tokens_in=100, tokens_out=20, tool_calls=1, success=True,
        final_text="", backend_tokens_in=900, backend_tokens_out=80,
    )
    assert rec.total_tokens_in == 1000
    assert rec.total_tokens_out == 100


def test_feedback_url_derived_from_mcp_url() -> None:
    """Feedback router is mounted under /system in app.api.router so the
    full path is /api/v1/system/feedback. Same host, swap the path."""
    assert (
        _feedback_url_from_mcp("http://localhost:8000/api/v1/mcp/sse")
        == "http://localhost:8000/api/v1/system/feedback"
    )
    assert (
        _feedback_url_from_mcp("https://spaider.example/api/v1/mcp/sse")
        == "https://spaider.example/api/v1/system/feedback"
    )


# ---------------------------------------------------------------------------
# DeepEval-style metrics
# ---------------------------------------------------------------------------


def test_normalize_qa_text_lowercases_strips_punct_and_articles() -> None:
    assert _normalize_qa_text("The Quick, Brown Fox.") == "quick brown fox"
    assert _normalize_qa_text("a NEW HOPE") == "new hope"
    assert _normalize_qa_text("an APPLE") == "apple"


def test_compute_f1_full_match_is_one() -> None:
    # After article-stripping and lowercase, both reduce to "answer is 42"
    assert _compute_f1("the answer is 42", "answer is 42") == 1.0


def test_compute_f1_zero_overlap_is_zero() -> None:
    assert _compute_f1("hello world", "goodbye moon") == 0.0


def test_compute_f1_partial_overlap_between_zero_and_one() -> None:
    f1 = _compute_f1("the cat sat on the mat", "cat on mat")
    # Overlap on cat/on/mat — partial, less than 1.0 because pred has extras.
    assert 0.5 < f1 < 1.0


def test_compute_f1_empty_strings() -> None:
    assert _compute_f1("", "") == 1.0
    assert _compute_f1("", "something") == 0.0
    assert _compute_f1("something", "") == 0.0


def test_compute_exact_match_basic() -> None:
    assert _compute_exact_match("YES", "yes") == 1.0
    assert _compute_exact_match("the answer", "answer") == 1.0  # article stripped
    assert _compute_exact_match("hello", "world") == 0.0


def test_compute_exact_match_punctuation_insensitive() -> None:
    assert _compute_exact_match("Tokyo, Japan!", "tokyo japan") == 1.0


def test_hotpotqa_tasks_load_with_composite_oracle() -> None:
    """the 24 hp_*.yaml files in tasks/hotpotqa/ all use the
    composite oracle and ship a non-empty expected_output."""
    cb_dir = Path(__file__).resolve().parent.parent / "tasks" / "hotpotqa"
    if not cb_dir.is_dir():
        pytest.skip("hotpotqa task dir not present")
    tasks = load_tasks(cb_dir)
    assert len(tasks) == 24, f"expected 24 hotpotqa tasks, got {len(tasks)}"
    for t in tasks:
        assert t.oracle_kind == "composite", f"{t.id} should be composite"
        assert (t.expected_output or "").strip(), f"{t.id} has empty expected_output"
        assert t.category == "hotpotqa", f"{t.id} category mismatch"


def test_corpus_artifacts_present() -> None:
    """Generated corpus files must be checked into the repo so consumers
    don't need Python to read them."""
    corpus_dir = Path(__file__).resolve().parent.parent / "corpus"
    assert (corpus_dir / "acmeai_30d.yaml").is_file()
    assert (corpus_dir / "acmeai_30d.txt").is_file()
    txt = (corpus_dir / "acmeai_30d.txt").read_text(encoding="utf-8")
    # Spot-check a story-arc fact (Stark) and a noise fact (PR-style line)
    # to confirm the generator wove both layers.
    assert "Stark Industries" in txt
    assert "[PR]" in txt


# ---------------------------------------------------------------------------
# ROUGE-L + retrieval_hit
# ---------------------------------------------------------------------------


def test_compute_rouge_l_full_match_is_one() -> None:
    # After article-stripping and lowercase, both reduce to "answer is 42";
    # LCS = 3 of both sequences → ROUGE-L = 1.0.
    assert _compute_rouge_l("the answer is 42", "answer is 42") == 1.0


def test_compute_rouge_l_paraphrase_does_not_zero() -> None:
    """When the model says the right thing surrounded by padding, ROUGE-L
    should give a non-zero (if small) score — same as F1. This metric is
    *not* a fix for the long-pred-short-gold mismatch; that requires GEval
    or a recall-weighted variant. ROUGE-L's value is order-preservation
    (see ``test_compute_rouge_l_order_matters``)."""
    pred = "Yes, both Scott Derrickson and Ed Wood were American directors"
    gold = "yes"
    rouge = _compute_rouge_l(pred, gold)
    assert rouge > 0.0


def test_compute_rouge_l_order_matters_unlike_f1() -> None:
    """ROUGE-L's distinctive property: LCS preserves token order. F1 ignores
    order. For ``red blue green`` vs ``green red blue`` (same tokens,
    different order), F1 = 1.0 but the LCS is only 2 (the matching contiguous
    subsequence is e.g. ``red blue``) — ROUGE-L < 1.0."""
    f1 = _compute_f1("red blue green", "green red blue")
    rouge = _compute_rouge_l("red blue green", "green red blue")
    assert f1 == 1.0  # F1 doesn't care about order — perfect score
    assert rouge < 1.0  # ROUGE-L does — order mismatch costs precision
    assert rouge > 0.5  # but still substantial — 2-token LCS out of 3


def test_compute_rouge_l_zero_overlap_is_zero() -> None:
    assert _compute_rouge_l("hello world", "goodbye moon") == 0.0


def test_compute_rouge_l_empty_strings() -> None:
    assert _compute_rouge_l("", "") == 1.0
    assert _compute_rouge_l("", "something") == 0.0
    assert _compute_rouge_l("something", "") == 0.0


def test_compute_rouge_l_subsequence_not_substring() -> None:
    # ROUGE-L cares about subsequences, not contiguous substrings. "cat …
    # mat" picks up via LCS (cat, mat), so ROUGE-L on "the cat sat on the
    # mat" vs gold "cat mat" should be 1.0 from the gold side (recall) and
    # less from the prediction side (precision).
    rouge = _compute_rouge_l("the cat sat on the mat", "cat mat")
    assert rouge > 0.5


def test_retrieval_hits_none_when_no_signal() -> None:
    """No supporting_titles AND no checkable expected_output — both None."""
    task = _task(properties={})
    assert _compute_retrieval_hits("anything", task) == (None, None)
    task = _task(properties={"hotpot_id": "abc"})  # has properties but no titles
    assert _compute_retrieval_hits("anything", task) == (None, None)


def test_retrieval_hits_subject_full_hit() -> None:
    task = _task(properties={"supporting_titles": ["Scott Derrickson", "Ed Wood"]})
    payload = (
        "Answer:\nYes, both directors were American.\n\n"
        "Top supporting facts:\n- Scott Derrickson is an American director.\n"
        "- Ed Wood was an American filmmaker."
    )
    _, subject = _compute_retrieval_hits(payload, task)
    assert subject == 1.0


def test_retrieval_hits_subject_partial_hit() -> None:
    task = _task(properties={"supporting_titles": ["Scott Derrickson", "Ed Wood"]})
    payload = "Answer: Scott Derrickson is American. (No mention of the other entity.)"
    _, subject = _compute_retrieval_hits(payload, task)
    assert subject == 0.5


def test_retrieval_hits_subject_case_insensitive() -> None:
    task = _task(properties={"supporting_titles": ["Scott Derrickson"]})
    _, subject = _compute_retrieval_hits("scott DERRICKSON is mentioned here", task)
    assert subject == 1.0


def test_retrieval_hits_empty_text_is_zero() -> None:
    task = _task(properties={"supporting_titles": ["Anything"]}, expected_output="Olivia")
    assert _compute_retrieval_hits("", task) == (0.0, 0.0)
    assert _compute_retrieval_hits(None, task) == (0.0, 0.0)


def test_retrieval_hits_honest_requires_answer_not_subject() -> None:
    """The acme_11 failure mode: the subject ('CTO') echoes back in the
    answer text while the actual answer node (Olivia) was never retrieved.
    The honest metric must report a miss where the subject metric reports
    a hit."""
    task = _task(
        properties={"supporting_titles": ["CTO"]},
        expected_output="Olivia",
    )
    payload = "Answer: The CTO is not mentioned in the graph context."
    honest, subject = _compute_retrieval_hits(payload, task)
    assert subject == 1.0   # lenient legacy metric: subject word present
    assert honest == 0.0    # honest metric: gold answer absent

    payload_with_answer = payload + "\nRelationships:\nOlivia -[ROLE]-> CTO"
    honest, _ = _compute_retrieval_hits(payload_with_answer, task)
    assert honest == 1.0


def test_retrieval_hits_yes_no_gold_falls_back_to_subject() -> None:
    """Substring-matching 'yes' in retrieved text is meaningless — yes/no
    golds fall back to the subject metric."""
    task = _task(
        properties={"supporting_titles": ["Scott Derrickson", "Ed Wood"]},
        expected_output="yes",
    )
    payload = "Scott Derrickson facts... Ed Wood facts... yes yes yes"
    honest, subject = _compute_retrieval_hits(payload, task)
    assert honest == subject == 1.0


def test_retrieval_hits_honest_without_titles() -> None:
    """Tasks with a contentful gold but no titles still get the honest metric."""
    task = _task(properties={}, expected_output="2026-06-25")
    honest, subject = _compute_retrieval_hits("window: 2026-06-25", task)
    assert honest == 1.0
    assert subject is None


def test_hotpotqa_yaml_loads_supporting_titles_into_properties() -> None:
    """The new ``properties`` field on Task is the ingress for retrieval_hit."""
    cb_dir = Path(__file__).resolve().parent.parent / "tasks" / "hotpotqa"
    if not cb_dir.is_dir():
        pytest.skip("hotpotqa task dir not present")
    tasks = load_tasks(cb_dir)
    for t in tasks:
        titles = t.properties.get("supporting_titles")
        assert titles, f"{t.id} should have supporting_titles in properties"
        assert isinstance(titles, list)
        assert all(isinstance(x, str) and x.strip() for x in titles)


# ---------------------------------------------------------------------------
# System-prompt + format-hint plumbing
# ---------------------------------------------------------------------------


def test_hotpotqa_yamls_have_format_hint_split_out_of_prompt() -> None:
    """After's restructure, the question-text and the answer-
    format-instruction live in separate YAML fields. The prompt should
    contain the question only — no 'answer in as few words' suffix."""
    cb_dir = Path(__file__).resolve().parent.parent / "tasks" / "hotpotqa"
    if not cb_dir.is_dir():
        pytest.skip("hotpotqa task dir not present")
    tasks = load_tasks(cb_dir)
    assert len(tasks) == 24
    for t in tasks:
        assert "as few words as possible" not in (t.prompt or "").lower(), (
            f"{t.id}: format hint leaked into prompt"
        )
        assert (t.format_hint or "").strip(), (
            f"{t.id}: missing format_hint after restructure"
        )
        assert "as few words as possible" in (t.format_hint or "").lower(), (
            f"{t.id}: format_hint should still carry the brevity instruction"
        )


def test_task_dataclass_default_system_prompt_is_none() -> None:
    """The runner's with-spaider default fires only when task.system_prompt
    is None. An empty string explicitly disables it; a non-empty string
    overrides. Verified via the dataclass defaults."""
    t = _task()
    assert t.system_prompt is None
    assert t.format_hint is None
    overridden = _task(system_prompt="custom", format_hint="brief")
    assert overridden.system_prompt == "custom"
    assert overridden.format_hint == "brief"


def test_default_with_spaider_system_prompt_mandates_retrieval() -> None:
    """The constant in runner.py is what flips the chronic tool_calls=0
    failures. Spot-check the wording so a future edit doesn't accidentally
    weaken it."""
    from benchmarks.runner import _DEFAULT_WITH_SPAIDER_SYSTEM_PROMPT
    assert _DEFAULT_WITH_SPAIDER_SYSTEM_PROMPT  # not empty
    p = _DEFAULT_WITH_SPAIDER_SYSTEM_PROMPT.lower()
    assert "spaider_query" in p
    assert "always" in p
    assert "do not answer from training" in p
