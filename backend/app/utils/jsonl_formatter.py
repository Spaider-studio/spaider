"""
JSONL export utilities for LLM fine-tuning dataset generation.
Supports OpenAI messages format and Alpaca instruction format.
"""
from __future__ import annotations

import json
import logging
from typing import Optional

logger = logging.getLogger(__name__)

# Approximate characters per token (conservative estimate for English text)
_CHARS_PER_TOKEN = 4


# ---------------------------------------------------------------------------
# Format converters
# ---------------------------------------------------------------------------

def to_openai_format(
    instruction: str,
    context: str,
    response: str,
    system_prompt: Optional[str] = None,
) -> str:
    """
    Serialize a training example to OpenAI's messages format (JSON line).

    Output:
        {"messages": [
            {"role": "system", "content": "..."},
            {"role": "user",   "content": "instruction\\n\\ncontext"},
            {"role": "assistant", "content": "response"}
        ]}

    Args:
        instruction: The task/question.
        context: Supporting context or background information.
        response: The expected answer or completion.
        system_prompt: Optional system message; defaults to a generic assistant prompt.

    Returns:
        A single JSON string (one JSONL line).
    """
    if system_prompt is None:
        system_prompt = (
            "You are a helpful assistant with deep knowledge of the subject matter."
        )

    user_content = instruction
    if context:
        user_content = f"{instruction}\n\nContext:\n{context}"

    record = {
        "messages": [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_content},
            {"role": "assistant", "content": response},
        ]
    }
    return json.dumps(record, ensure_ascii=False)


def to_alpaca_format(
    instruction: str,
    context: str,
    response: str,
) -> str:
    """
    Serialize a training example to Alpaca format (JSON line).

    Output:
        {"instruction": "...", "input": "...", "output": "..."}

    Args:
        instruction: The task/question.
        context: The input/context field (maps to Alpaca's 'input').
        response: The expected output/answer.

    Returns:
        A single JSON string (one JSONL line).
    """
    record = {
        "instruction": instruction,
        "input": context,
        "output": response,
    }
    return json.dumps(record, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Validation
# ---------------------------------------------------------------------------

def validate_jsonl(content: str) -> tuple[bool, list[str]]:
    """
    Validate a JSONL string (one JSON object per line).

    Args:
        content: Multi-line JSONL string.

    Returns:
        (valid: bool, errors: list[str])
        valid is True only if ALL lines are valid JSON objects.
        errors contains descriptions of all found problems.
    """
    errors: list[str] = []

    if not content or not content.strip():
        errors.append("Content is empty.")
        return False, errors

    lines = content.splitlines()
    for line_num, line in enumerate(lines, start=1):
        line = line.strip()
        if not line:
            continue  # skip blank lines

        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            errors.append(f"Line {line_num}: JSON parse error: {exc}")
            continue

        if not isinstance(obj, dict):
            errors.append(
                f"Line {line_num}: expected a JSON object (dict), got {type(obj).__name__}."
            )

    valid = len(errors) == 0
    return valid, errors


# ---------------------------------------------------------------------------
# Token estimation
# ---------------------------------------------------------------------------

def count_tokens_approx(text: str) -> int:
    """
    Approximate token count for a string using a simple character-based heuristic.
    Uses ~4 characters per token as a conservative estimate for English text.

    Args:
        text: Input text string.

    Returns:
        Estimated token count (integer).
    """
    if not text:
        return 0
    return max(1, len(text) // _CHARS_PER_TOKEN)


def count_jsonl_tokens_approx(jsonl_content: str) -> dict[str, int]:
    """
    Estimate total and per-field token counts across all JSONL records.

    Returns:
        Dict with keys: "total", "instruction", "context", "response"
        (for Alpaca format) or "total", "system", "user", "assistant"
        (for OpenAI format).
    """
    counts: dict[str, int] = {"total": 0}
    lines = [ln.strip() for ln in jsonl_content.splitlines() if ln.strip()]

    for line in lines:
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue

        # OpenAI format
        if "messages" in obj:
            for msg in obj["messages"]:
                role = msg.get("role", "unknown")
                content = msg.get("content", "")
                tokens = count_tokens_approx(content)
                counts[role] = counts.get(role, 0) + tokens
                counts["total"] += tokens
        # Alpaca format
        else:
            for field in ("instruction", "input", "output"):
                val = obj.get(field, "")
                tokens = count_tokens_approx(val)
                counts[field] = counts.get(field, 0) + tokens
                counts["total"] += tokens

    return counts
