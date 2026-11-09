"""
The eval gate: a fine-tuned model is served only if it measurably beats the base.

Floors are fixed on the server. A request cannot lower them, and promotion checks
the stored parameters again, so a value changed after evaluation cannot get a
weak model through.
"""
from __future__ import annotations

import random
import re
from dataclasses import asdict, dataclass
from typing import Optional

CONFIDENCE_FLOOR = 0.9
MIN_DELTA_FLOOR = 0.0
MIN_ITEMS_FLOOR = 20
_BOOTSTRAP_ROUNDS = 2000
_WORD = re.compile(r"\w+")


class GateRejected(ValueError):
    """Parameters fall below the server's floors."""


@dataclass
class GateParams:
    confidence: float = CONFIDENCE_FLOOR
    min_delta: float = MIN_DELTA_FLOOR
    memory: bool = True
    min_items: int = MIN_ITEMS_FLOOR


@dataclass
class GateResult:
    passed: bool
    items: int
    base_score: float
    candidate_score: float
    mean_delta: float
    confidence: float


def validate(params: GateParams) -> None:
    if params.confidence < CONFIDENCE_FLOOR:
        raise GateRejected(f"confidence must be at least {CONFIDENCE_FLOOR}")
    if params.min_delta < MIN_DELTA_FLOOR:
        raise GateRejected(f"min_delta must be at least {MIN_DELTA_FLOOR}")
    if params.memory is not True:
        raise GateRejected("evaluation must run with memory on")
    if params.min_items < MIN_ITEMS_FLOOR:
        raise GateRejected(f"min_items must be at least {MIN_ITEMS_FLOOR}")


def f1(answer: str, expected: str) -> float:
    """Token overlap between an answer and the expected text."""
    predicted, gold = _WORD.findall(answer.lower()), _WORD.findall(expected.lower())
    if not predicted or not gold:
        return 0.0
    remaining = list(gold)
    common = 0
    for token in predicted:
        if token in remaining:
            remaining.remove(token)
            common += 1
    if common == 0:
        return 0.0
    precision, recall = common / len(predicted), common / len(gold)
    return 2 * precision * recall / (precision + recall)


def decide(base: list[float], candidate: list[float], params: GateParams, seed: str) -> GateResult:
    """Paired bootstrap: how often does a resample's mean improvement meet min_delta?"""
    validate(params)
    if len(base) != len(candidate):
        raise ValueError("score lists must be paired")
    deltas = [c - b for b, c in zip(base, candidate)]
    count = len(deltas)
    rng = random.Random(seed)
    wins = 0
    if count:
        for _ in range(_BOOTSTRAP_ROUNDS):
            resample = [deltas[rng.randrange(count)] for _ in range(count)]
            # Strict: a resample with no improvement is not evidence of one.
            if sum(resample) / count > params.min_delta:
                wins += 1
    confidence = wins / _BOOTSTRAP_ROUNDS if count else 0.0
    mean_delta = sum(deltas) / count if count else 0.0
    return GateResult(
        passed=count >= params.min_items and confidence >= params.confidence and mean_delta > params.min_delta,
        items=count,
        base_score=sum(base) / count if count else 0.0,
        candidate_score=sum(candidate) / count if count else 0.0,
        mean_delta=mean_delta,
        confidence=confidence,
    )


def recheck(params: Optional[dict], result: Optional[dict]) -> bool:
    """At promotion time: is what is stored still good enough?"""
    if not params or not result:
        return False
    try:
        stored = GateParams(**params)
        validate(stored)
    except (TypeError, GateRejected):
        return False
    return (
        result.get("passed") is True
        and int(result.get("items", 0)) >= max(stored.min_items, MIN_ITEMS_FLOOR)
        and float(result.get("confidence", 0)) >= max(stored.confidence, CONFIDENCE_FLOOR)
        and float(result.get("mean_delta", 0)) > max(stored.min_delta, MIN_DELTA_FLOOR)
    )


def as_dict(value) -> dict:
    return asdict(value)
