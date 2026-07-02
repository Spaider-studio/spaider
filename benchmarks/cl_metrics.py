"""
Continual-learning metrics for the SpAIder task-sequence benchmark.

Pure functions: they take an accuracy matrix and a couple of vectors and return
the standard continual-learning summary (Lopez-Paz & Ranzato, GEM 2017), adapted
to a memory system where "learning task i" means ingesting task i's corpus.

Definitions (tasks ordered 0..T-1)
----------------------------------
- R[i][j]  : accuracy on task j's probes, measured AFTER ingesting task i.
             Defined for j <= i (lower-triangular). None above the diagonal.
- baseline : accuracy on each task's probes on an EMPTY graph (cold, before any
             ingest). For a private corpus the model cannot know, this is ~0.
- pre[j]   : accuracy on task j measured just BEFORE ingesting j, i.e. after
             ingesting 0..j-1. pre[0] == baseline[0]. Captures forward transfer.

Reported metrics
----------------
- final_accuracy   ACC = mean_j R[T-1][j]                     higher is better
- average_forgetting  mean_j ( max_{i in [j, T-2]} R[i][j] - R[T-1][j] )
                     how much peak accuracy on earlier tasks was lost by the end.
                     Lower is better; <= 0 means nothing was forgotten.
- backward_transfer  BWT = mean_{j<T-1} ( R[T-1][j] - R[j][j] )
                     effect of later learning on earlier tasks. >0 helps, <0 hurts.
- forward_transfer   FWT = mean_{j>=1} ( pre[j] - baseline[j] )
                     effect of earlier learning on a task before it is ingested.
                     >0 means prior knowledge already answered the new task.

These are deliberately metric-agnostic: the caller decides what "accuracy" is
(mean F1, exact-match rate, etc.) and passes the resulting scalars in.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Optional


@dataclass
class CLReport:
    task_ids: list[str]
    final_accuracy: float
    average_forgetting: float
    backward_transfer: float
    forward_transfer: Optional[float]
    per_task_final: dict[str, float] = field(default_factory=dict)
    per_task_forgetting: dict[str, float] = field(default_factory=dict)
    accuracy_matrix: list[list[Optional[float]]] = field(default_factory=list)
    baseline: list[float] = field(default_factory=list)


def _mean(xs: list[float]) -> float:
    return sum(xs) / len(xs) if xs else 0.0


def compute_cl_metrics(
    task_ids: list[str],
    matrix: list[list[Optional[float]]],
    baseline: list[float],
    pre: Optional[list[float]] = None,
) -> CLReport:
    """Compute the continual-learning summary from a filled accuracy matrix.

    Args:
        task_ids: ordered task identifiers, length T.
        matrix:   T x T lower-triangular accuracy matrix. matrix[i][j] is the
                  accuracy on task j after ingesting task i, for j <= i; entries
                  above the diagonal are ignored (pass None or any placeholder).
        baseline: length-T cold accuracy per task (empty graph).
        pre:      optional length-T "just before ingest" accuracy per task; when
                  omitted, forward_transfer is None.

    Raises:
        ValueError on shape mismatch (fail loud rather than silently mis-report).
    """
    t = len(task_ids)
    if t == 0:
        raise ValueError("task_ids is empty")
    if len(matrix) != t or any(len(row) != t for row in matrix):
        raise ValueError(f"matrix must be {t}x{t}")
    if len(baseline) != t:
        raise ValueError(f"baseline must have length {t}")
    if pre is not None and len(pre) != t:
        raise ValueError(f"pre must have length {t}")

    last = t - 1

    # Final accuracy per task (bottom row of the matrix).
    per_task_final = {task_ids[j]: float(matrix[last][j]) for j in range(t)}
    final_accuracy = _mean(list(per_task_final.values()))

    # Forgetting + BWT are only defined for earlier tasks (j < last).
    per_task_forgetting: dict[str, float] = {}
    bwt_terms: list[float] = []
    for j in range(last):
        # Peak accuracy on task j across the runs where it had been learned
        # (rows j .. last-1), minus its final accuracy.
        peak = max(float(matrix[i][j]) for i in range(j, last))
        per_task_forgetting[task_ids[j]] = peak - float(matrix[last][j])
        bwt_terms.append(float(matrix[last][j]) - float(matrix[j][j]))

    average_forgetting = _mean(list(per_task_forgetting.values()))
    backward_transfer = _mean(bwt_terms)

    forward_transfer: Optional[float] = None
    if pre is not None and t >= 2:
        forward_transfer = _mean([pre[j] - baseline[j] for j in range(1, t)])

    return CLReport(
        task_ids=list(task_ids),
        final_accuracy=final_accuracy,
        average_forgetting=average_forgetting,
        backward_transfer=backward_transfer,
        forward_transfer=forward_transfer,
        per_task_final=per_task_final,
        per_task_forgetting=per_task_forgetting,
        accuracy_matrix=matrix,
        baseline=list(baseline),
    )
