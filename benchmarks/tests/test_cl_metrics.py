"""Offline unit tests for the continual-learning metrics (no stack needed)."""
import math

import pytest

from benchmarks.continual.cl_metrics import compute_cl_metrics


def test_two_task_forgetting_and_transfer():
    # Tasks [A, B].
    #   R[0][0]=1.0  accuracy on A after learning A
    #   R[1][0]=0.6  accuracy on A after learning B (dropped -> forgetting)
    #   R[1][1]=1.0  accuracy on B after learning B
    matrix = [
        [1.0, None],
        [0.6, 1.0],
    ]
    baseline = [0.0, 0.0]      # cold, private corpus
    pre = [0.0, 0.2]           # before learning B, A already answers 0.2 of B

    r = compute_cl_metrics(["A", "B"], matrix, baseline, pre=pre)

    assert math.isclose(r.final_accuracy, 0.8)            # mean(0.6, 1.0)
    assert math.isclose(r.average_forgetting, 0.4)        # 1.0 - 0.6 on A
    assert math.isclose(r.backward_transfer, -0.4)        # 0.6 - 1.0
    assert math.isclose(r.forward_transfer, 0.2)          # 0.2 - 0.0
    assert math.isclose(r.per_task_forgetting["A"], 0.4)
    assert math.isclose(r.per_task_final["A"], 0.6)
    assert math.isclose(r.per_task_final["B"], 1.0)


def test_no_forgetting_perfect_retention():
    matrix = [
        [1.0, None],
        [1.0, 1.0],
    ]
    r = compute_cl_metrics(["A", "B"], matrix, [0.0, 0.0], pre=[0.0, 0.0])
    assert math.isclose(r.average_forgetting, 0.0)
    assert math.isclose(r.backward_transfer, 0.0)
    assert math.isclose(r.final_accuracy, 1.0)


def test_forward_transfer_none_when_pre_absent():
    matrix = [[1.0, None], [0.5, 1.0]]
    r = compute_cl_metrics(["A", "B"], matrix, [0.0, 0.0])
    assert r.forward_transfer is None


def test_single_task_has_no_forgetting():
    r = compute_cl_metrics(["A"], [[1.0]], [0.0], pre=[0.0])
    assert math.isclose(r.final_accuracy, 1.0)
    assert math.isclose(r.average_forgetting, 0.0)
    assert math.isclose(r.backward_transfer, 0.0)


def test_shape_mismatch_raises():
    with pytest.raises(ValueError):
        compute_cl_metrics(["A", "B"], [[1.0]], [0.0, 0.0])
    with pytest.raises(ValueError):
        compute_cl_metrics(["A"], [[1.0]], [0.0, 0.0])
