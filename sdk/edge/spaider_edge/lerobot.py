"""
LeRobot v2 dataset metadata to and from memory episodes.

Only metadata is exchanged: one memory episode per dataset episode, carrying its
task text. Frames, video and parquet data are left where they are.
"""
from __future__ import annotations

import json
from pathlib import Path
from typing import Dict, List

from spaider_edge.memory import EdgeMemory

_KIND = "lerobot_episode"


def _read_jsonl(path: Path) -> List[dict]:
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def import_dataset(root: str, memory: EdgeMemory, *, session_id: str = "") -> int:
    """Add each dataset episode to memory. Importing twice adds nothing new."""
    base = Path(root)
    info = json.loads((base / "meta" / "info.json").read_text(encoding="utf-8"))
    dataset = session_id or base.name
    imported = 0
    for episode in _read_jsonl(base / "meta" / "episodes.jsonl"):
        tasks = episode.get("tasks") or []
        text = "; ".join(tasks) if tasks else f"episode {episode['episode_index']}"
        memory.remember(
            text,
            kind=_KIND,
            session_id=dataset,
            source="lerobot",
            idempotency_key=f"lerobot:{dataset}:{episode['episode_index']}",
            metadata={
                "episode_index": episode["episode_index"],
                "length": episode.get("length"),
                "fps": info.get("fps"),
                "robot_type": info.get("robot_type"),
            },
        )
        imported += 1
    return imported


def export_dataset(memory: EdgeMemory, root: str, *, robot_type: str = "unknown", fps: int = 30) -> int:
    """Write memory's LeRobot episodes as dataset metadata."""
    episodes = sorted(
        memory.recall(limit=1_000_000, kind=_KIND),
        key=lambda item: item["metadata"].get("episode_index", 0),
    )
    meta = Path(root) / "meta"
    meta.mkdir(parents=True, exist_ok=True)
    task_index: Dict[str, int] = {}
    lines = []
    for position, episode in enumerate(episodes):
        tasks = episode["text"].split("; ")
        for task in tasks:
            task_index.setdefault(task, len(task_index))
        lines.append(
            json.dumps(
                {
                    "episode_index": position,
                    "tasks": tasks,
                    "length": episode["metadata"].get("length") or 0,
                }
            )
        )
    (meta / "episodes.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")
    (meta / "tasks.jsonl").write_text(
        "\n".join(json.dumps({"task_index": index, "task": task}) for task, index in task_index.items()) + "\n",
        encoding="utf-8",
    )
    (meta / "info.json").write_text(
        json.dumps(
            {
                "codebase_version": "v2.0",
                "robot_type": robot_type,
                "fps": fps,
                "total_episodes": len(episodes),
                "total_tasks": len(task_index),
            },
            indent=2,
        ),
        encoding="utf-8",
    )
    return len(episodes)
