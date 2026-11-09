"""A learning-curve check on a deterministic simulation; no real robot is involved."""
from __future__ import annotations

from benchmarks.robotics.simulate import compare, run


def test_same_seed_same_result():
    assert run(3, use_memory=True, sessions=3) == run(3, use_memory=True, sessions=3)


def test_different_seeds_differ():
    assert run(3, use_memory=True, sessions=3) != run(4, use_memory=True, sessions=3)


def test_memory_improves_over_sessions_and_beats_no_memory():
    report = compare([1, 2, 3, 4, 5])
    curve, baseline = report["steps_per_fetch_with_memory"], report["steps_per_fetch_without_memory"]
    assert sum(curve[-3:]) / 3 < curve[0], curve
    assert sum(curve[-3:]) / 3 < sum(baseline[-3:]) / 3, (curve, baseline)


def test_without_memory_there_is_no_learning():
    baseline = compare([1, 2, 3, 4, 5])["steps_per_fetch_without_memory"]
    assert abs(baseline[-1] - baseline[0]) < baseline[0] * 0.5
