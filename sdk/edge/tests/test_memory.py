from __future__ import annotations

import json
import threading
from pathlib import Path

import pytest

from spaider_edge import EdgeMemory, hash_embed
from spaider_edge.lerobot import export_dataset, import_dataset


@pytest.fixture
def memory():
    store = EdgeMemory()
    yield store
    store.close()


class RecordingTransport:
    def __init__(self, fail_after=None):
        self.batches = []
        self.fail_after = fail_after

    def post(self, path, body):
        if self.fail_after is not None and len(self.batches) >= self.fail_after:
            raise ConnectionError("link down")
        self.batches.append((path, body))
        return {}

    def get(self, path):
        return {"nodes": [{"id": "n1", "label": "Charging bay", "type": "PLACE", "description": "charging bay in hall"}]}


def test_vectors_match_the_server_golden_file():
    golden = json.loads((Path(__file__).parent / "golden_hash.json").read_text(encoding="utf-8"))
    for case in golden:
        assert hash_embed(case["text"]) == pytest.approx(case["vector"], abs=1e-12)


def test_remember_and_recall_by_text(memory):
    memory.remember("the charger is behind the blue door")
    memory.remember("lunch was soup")
    assert memory.recall("blue door charger")[0]["text"].startswith("the charger")


def test_empty_content_is_refused(memory):
    with pytest.raises(ValueError):
        memory.remember("   ")


def test_repeated_key_returns_the_first_id(memory):
    first = memory.remember("once", idempotency_key="k")
    assert memory.remember("again", idempotency_key="k") == first
    assert memory.count() == 1


def test_recall_near_a_point_uses_true_distance(memory):
    memory.remember("inside", location={"x": 1.0, "y": 1.0})
    memory.remember("corner of the box but outside the circle", location={"x": 2.9, "y": 2.9})
    memory.remember("far", location={"x": 50.0, "y": 50.0})
    found = memory.recall(near={"x": 0.0, "y": 0.0}, radius=3.0)
    assert [item["text"] for item in found] == ["inside"]


def test_recall_near_orders_by_distance(memory):
    memory.remember("two", location={"x": 2.0, "y": 0.0})
    memory.remember("one", location={"x": 1.0, "y": 0.0})
    assert [item["text"] for item in memory.recall(near={"x": 0.0, "y": 0.0}, radius=5.0)] == ["one", "two"]


def test_recall_near_in_three_dimensions(memory):
    memory.remember("upstairs", location={"x": 0.0, "y": 0.0, "z": 10.0})
    assert memory.recall(near={"x": 0.0, "y": 0.0, "z": 0.0}, radius=5.0) == []


def test_geographic_recall_is_in_metres(memory):
    memory.remember("close", location={"latitude": 52.5200, "longitude": 13.4050})
    memory.remember("other city", location={"latitude": 48.1351, "longitude": 11.5820})
    found = memory.recall(near={"latitude": 52.5201, "longitude": 13.4051}, radius=100)
    assert [item["text"] for item in found] == ["close"]
    assert found[0]["distance"] < 100


def test_recall_filters_by_kind_and_session(memory):
    memory.remember("door open", kind="vision", session_id="a")
    memory.remember("door closed", kind="audio", session_id="b")
    assert [i["text"] for i in memory.recall("door", kind="audio")] == ["door closed"]
    assert [i["text"] for i in memory.recall("door", session_id="a")] == ["door open"]


def test_incomplete_location_is_refused_and_nothing_is_stored(memory):
    with pytest.raises(ValueError):
        memory.remember("x", location={"x": 1.0})
    assert memory.count() == 0


def test_memory_survives_reopening(tmp_path):
    path = str(tmp_path / "robot.db")
    store = EdgeMemory(path)
    store.remember("persisted", location={"x": 1.0, "y": 1.0})
    store.close()
    reopened = EdgeMemory(path)
    assert reopened.recall("persisted")[0]["text"] == "persisted"
    assert reopened.recall(near={"x": 1.0, "y": 1.0}, radius=1.0)
    reopened.close()


def test_sync_pushes_in_batches_once(memory):
    for index in range(5):
        memory.remember(f"event {index}", location={"x": 1.0, "y": 2.0})
    transport = RecordingTransport()
    assert memory.sync(transport, "kb", batch_size=2) == {"pushed": 5, "pending": 0}
    assert [len(body["episodes"]) for _, body in transport.batches] == [2, 2, 1]
    path, body = transport.batches[0]
    assert path == "/api/v1/episodes" and body["agent_id"] == "kb"
    assert body["episodes"][0]["location"] == {"x": 1.0, "y": 2.0} and body["episodes"][0]["idempotency_key"]
    assert memory.sync(transport, "kb") == {"pushed": 0, "pending": 0}


def test_a_dropped_link_keeps_the_rest_for_later(memory):
    for index in range(4):
        memory.remember(f"event {index}")
    assert memory.sync(RecordingTransport(fail_after=1), "kb", batch_size=2) == {"pushed": 2, "pending": 2}
    assert memory.sync(RecordingTransport(), "kb", batch_size=2) == {"pushed": 2, "pending": 0}


def test_snapshot_is_searchable_offline(memory):
    assert memory.pull_snapshot(RecordingTransport(), "kb") == 1
    result = memory.recall("charging bay")
    assert result[0]["type"] == "node" and result[0]["id"] == "n1"


def test_a_new_snapshot_replaces_the_old_one(memory):
    memory.load_snapshot({"nodes": [{"id": "old", "label": "old thing"}]})
    memory.load_snapshot({"nodes": [{"id": "new", "label": "new thing"}]})
    assert [item["id"] for item in memory.recall("thing")] == ["new"]


def test_concurrent_writers(memory):
    def write(worker):
        for index in range(50):
            memory.remember(f"w{worker} {index}", location={"x": float(index), "y": 0.0})

    threads = [threading.Thread(target=write, args=(worker,)) for worker in range(4)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert memory.count() == 200


def _dataset(root: Path):
    meta = root / "meta"
    meta.mkdir(parents=True)
    (meta / "info.json").write_text(json.dumps({"fps": 10, "robot_type": "so100"}), encoding="utf-8")
    episodes = [{"episode_index": 0, "tasks": ["pick the cube"], "length": 100}, {"episode_index": 1, "tasks": ["open drawer", "close drawer"], "length": 50}]
    (meta / "episodes.jsonl").write_text("\n".join(json.dumps(e) for e in episodes) + "\n", encoding="utf-8")


def test_lerobot_import_is_idempotent(memory, tmp_path):
    _dataset(tmp_path / "ds")
    assert import_dataset(str(tmp_path / "ds"), memory) == 2
    import_dataset(str(tmp_path / "ds"), memory)
    assert memory.count() == 2
    assert memory.recall("cube")[0]["metadata"]["robot_type"] == "so100"


def test_lerobot_round_trip_keeps_tasks(memory, tmp_path):
    _dataset(tmp_path / "ds")
    import_dataset(str(tmp_path / "ds"), memory)
    assert export_dataset(memory, str(tmp_path / "out"), robot_type="so100", fps=10) == 2
    episodes = [json.loads(line) for line in (tmp_path / "out/meta/episodes.jsonl").read_text().splitlines()]
    assert episodes[1]["tasks"] == ["open drawer", "close drawer"] and episodes[0]["length"] == 100
    info = json.loads((tmp_path / "out/meta/info.json").read_text())
    assert info["total_episodes"] == 2 and info["total_tasks"] == 3
