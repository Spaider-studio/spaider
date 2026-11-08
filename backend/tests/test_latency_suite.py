"""The latency benchmark must keep running; this only checks it completes."""
from __future__ import annotations

import importlib.util
from pathlib import Path


async def test_latency_suite_runs_on_a_small_graph(neo4j_test_driver, tmp_path, monkeypatch):
    from app.config import settings
    from app.services import graph_service as graph_module

    monkeypatch.setattr(settings, "spaider_insecure_dev", False)
    monkeypatch.setattr(graph_module, "_shared_graph_service", None)
    path = Path(__file__).resolve().parents[2] / "benchmarks" / "latency" / "run_latency.py"
    spec = importlib.util.spec_from_file_location("run_latency", path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    report = await module.run(100, 3, tmp_path / "report.json")
    assert (tmp_path / "report.json").exists()
    assert {"episode_write@1", "recall_near@8", "recall_federated@8"} <= set(report["results"])
