"""SQLite-backed episodic memory for a single device."""
from __future__ import annotations

import array
import json
import math
import sqlite3
import threading
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Iterable, List, Mapping, Optional, Protocol, Tuple

from spaider_edge.hashing import HASH_EMBEDDING_DIM, hash_embed

_SCHEMA = """
CREATE TABLE IF NOT EXISTS episodes (
    rowid INTEGER PRIMARY KEY AUTOINCREMENT,
    id TEXT UNIQUE NOT NULL,
    idempotency_key TEXT UNIQUE NOT NULL,
    content TEXT NOT NULL,
    kind TEXT NOT NULL,
    session_id TEXT,
    source TEXT,
    occurred_at TEXT NOT NULL,
    metadata TEXT NOT NULL,
    latitude REAL, longitude REAL, x REAL, y REAL, z REAL,
    embedding BLOB NOT NULL,
    synced INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS episodes_pending ON episodes (synced, rowid);
CREATE VIRTUAL TABLE IF NOT EXISTS episodes_rtree USING rtree(rowid, min_x, max_x, min_y, max_y);
CREATE TABLE IF NOT EXISTS snapshot_nodes (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    type TEXT,
    description TEXT,
    embedding BLOB NOT NULL
);
"""

_EARTH_RADIUS_M = 6_371_000.0
_METRES_PER_DEGREE = 111_320.0
_SYNC_BATCH = 200


class Transport(Protocol):
    def post(self, path: str, body: dict) -> dict: ...

    def get(self, path: str) -> dict: ...


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _blob(vector: List[float]) -> bytes:
    return array.array("f", vector).tobytes()


def _unblob(data: bytes) -> array.array:
    vector = array.array("f")
    vector.frombytes(data)
    return vector


def _haversine(lat1: float, lon1: float, lat2: float, lon2: float) -> float:
    phi1, phi2 = math.radians(lat1), math.radians(lat2)
    half_dphi = math.radians(lat2 - lat1) / 2
    half_dlam = math.radians(lon2 - lon1) / 2
    a = math.sin(half_dphi) ** 2 + math.cos(phi1) * math.cos(phi2) * math.sin(half_dlam) ** 2
    return 2 * _EARTH_RADIUS_M * math.asin(math.sqrt(a))


def _is_geographic(location: Mapping[str, float]) -> bool:
    return location.get("latitude") is not None and location.get("longitude") is not None


def _plane(location: Mapping[str, float]) -> Tuple[float, float]:
    if _is_geographic(location):
        return float(location["longitude"]), float(location["latitude"])
    if location.get("x") is None or location.get("y") is None:
        raise ValueError("A location needs latitude and longitude, or x and y.")
    return float(location["x"]), float(location["y"])


class EdgeMemory:
    """Safe to share between threads; each call takes the connection lock."""

    def __init__(self, path: str = ":memory:", *, dim: int = HASH_EMBEDDING_DIM) -> None:
        self._dim = dim
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.row_factory = sqlite3.Row
        with self._lock:
            self._db.executescript(_SCHEMA)
            if path != ":memory:":
                self._db.execute("PRAGMA journal_mode=WAL")
            self._db.commit()

    def close(self) -> None:
        with self._lock:
            self._db.close()

    # ── Writing ───────────────────────────────────────────────────────────

    def remember(
        self,
        content: str,
        *,
        kind: str = "observation",
        session_id: Optional[str] = None,
        source: Optional[str] = None,
        occurred_at: Optional[str] = None,
        location: Optional[Mapping[str, float]] = None,
        metadata: Optional[Mapping[str, Any]] = None,
        idempotency_key: Optional[str] = None,
    ) -> str:
        """Store an episode and return its id. A repeated key returns the first id."""
        if not content or not content.strip():
            raise ValueError("content must not be empty")
        episode_id = str(uuid.uuid4())
        key = idempotency_key or episode_id
        location = location or {}
        with self._lock:
            with self._db:
                cursor = self._db.execute(
                    """
                    INSERT OR IGNORE INTO episodes
                    (id, idempotency_key, content, kind, session_id, source, occurred_at, metadata,
                     latitude, longitude, x, y, z, embedding)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        episode_id, key, content, kind, session_id, source, occurred_at or _now(),
                        json.dumps(dict(metadata or {}), default=str),
                        location.get("latitude"), location.get("longitude"),
                        location.get("x"), location.get("y"), location.get("z"),
                        _blob(hash_embed(content, self._dim)),
                    ),
                )
                if cursor.rowcount == 0:
                    row = self._db.execute("SELECT id FROM episodes WHERE idempotency_key = ?", (key,)).fetchone()
                    return row["id"]
                if location:
                    x, y = _plane(location)
                    self._db.execute(
                        "INSERT INTO episodes_rtree (rowid, min_x, max_x, min_y, max_y) VALUES (?, ?, ?, ?, ?)",
                        (cursor.lastrowid, x, x, y, y),
                    )
        return episode_id

    # ── Reading ───────────────────────────────────────────────────────────

    def recall(
        self,
        text: str = "",
        *,
        limit: int = 10,
        near: Optional[Mapping[str, float]] = None,
        radius: float = 1.0,
        kind: Optional[str] = None,
        session_id: Optional[str] = None,
        scan_limit: int = 20_000,
    ) -> List[Dict[str, Any]]:
        """Newest-first candidates are scored against ``text``; ``near`` limits by place."""
        with self._lock:
            rows = self._near_rows(near, radius) if near else self._recent_rows(scan_limit)
            nodes = [] if near or not text else self._db.execute("SELECT * FROM snapshot_nodes").fetchall()
        results = []
        query = _unblob(_blob(hash_embed(text, self._dim))) if text else None
        for row in rows:
            if kind and row["kind"] != kind or session_id and row["session_id"] != session_id:
                continue
            item = self._episode(row)
            if near:
                item["distance"] = self._distance(row, near)
                if item["distance"] > radius:
                    continue
            item["score"] = self._score(query, row["embedding"])
            results.append(item)
        for node in nodes:
            results.append(
                {
                    "type": "node",
                    "id": node["id"],
                    "text": node["description"] or node["label"],
                    "kind": node["type"],
                    "score": self._score(query, node["embedding"]),
                }
            )
        if near:
            results.sort(key=lambda item: item["distance"])
        elif query is not None:
            results.sort(key=lambda item: item["score"], reverse=True)
            results = [item for item in results if item["score"] > 0]
        return results[:limit]

    def _recent_rows(self, scan_limit: int) -> List[sqlite3.Row]:
        return self._db.execute("SELECT * FROM episodes ORDER BY rowid DESC LIMIT ?", (scan_limit,)).fetchall()

    def _near_rows(self, near: Mapping[str, float], radius: float) -> List[sqlite3.Row]:
        x, y = _plane(near)
        reach_x = reach_y = radius
        if _is_geographic(near):
            reach_y = radius / _METRES_PER_DEGREE
            reach_x = reach_y / max(math.cos(math.radians(y)), 1e-6)
        return self._db.execute(
            """
            SELECT e.* FROM episodes e JOIN episodes_rtree t ON e.rowid = t.rowid
            WHERE t.min_x <= ? AND t.max_x >= ? AND t.min_y <= ? AND t.max_y >= ?
            """,
            (x + reach_x, x - reach_x, y + reach_y, y - reach_y),
        ).fetchall()

    @staticmethod
    def _distance(row: sqlite3.Row, near: Mapping[str, float]) -> float:
        if _is_geographic(near):
            if row["latitude"] is None:
                return math.inf
            return _haversine(row["latitude"], row["longitude"], near["latitude"], near["longitude"])
        if row["x"] is None:
            return math.inf
        dz = (row["z"] or 0.0) - (near.get("z") or 0.0)
        return math.sqrt((row["x"] - near["x"]) ** 2 + (row["y"] - near["y"]) ** 2 + dz**2)

    @staticmethod
    def _score(query: Optional[array.array], embedding: bytes) -> float:
        if query is None:
            return 0.0
        return round(sum(a * b for a, b in zip(query, _unblob(embedding))), 6)

    @staticmethod
    def _episode(row: sqlite3.Row) -> Dict[str, Any]:
        return {
            "type": "episode",
            "id": row["id"],
            "text": row["content"],
            "kind": row["kind"],
            "session_id": row["session_id"],
            "source": row["source"],
            "occurred_at": row["occurred_at"],
            "metadata": json.loads(row["metadata"]),
            "location": {
                key: row[key] for key in ("latitude", "longitude", "x", "y", "z") if row[key] is not None
            }
            or None,
        }

    def count(self, *, pending_only: bool = False) -> int:
        with self._lock:
            where = "WHERE synced = 0" if pending_only else ""
            return self._db.execute(f"SELECT count(*) AS c FROM episodes {where}").fetchone()["c"]

    # ── Sync ──────────────────────────────────────────────────────────────

    def _pending(self, limit: int) -> List[sqlite3.Row]:
        with self._lock:
            return self._db.execute(
                "SELECT * FROM episodes WHERE synced = 0 ORDER BY rowid LIMIT ?", (limit,)
            ).fetchall()

    def _mark_synced(self, ids: Iterable[str]) -> None:
        with self._lock:
            with self._db:
                self._db.executemany("UPDATE episodes SET synced = 1 WHERE id = ?", [(i,) for i in ids])

    @staticmethod
    def _wire(row: sqlite3.Row) -> Dict[str, Any]:
        episode: Dict[str, Any] = {
            "content": row["content"],
            "kind": row["kind"],
            "idempotency_key": row["idempotency_key"],
            "session_id": row["session_id"],
            "source": row["source"],
            "occurred_at": row["occurred_at"],
            "metadata": json.loads(row["metadata"]),
        }
        location = {key: row[key] for key in ("latitude", "longitude", "x", "y", "z") if row[key] is not None}
        if location:
            episode["location"] = location
        return episode

    def sync(self, transport: Transport, agent_id: str, *, batch_size: int = _SYNC_BATCH) -> Dict[str, int]:
        """Push pending episodes in order. A failure stops there and is retried next time."""
        pushed = 0
        while True:
            rows = self._pending(batch_size)
            if not rows:
                return {"pushed": pushed, "pending": 0}
            try:
                transport.post("/api/v1/episodes", {"agent_id": agent_id, "episodes": [self._wire(r) for r in rows]})
            except Exception:  # noqa: BLE001 — unreliable links are the normal case here
                return {"pushed": pushed, "pending": self.count(pending_only=True)}
            self._mark_synced(row["id"] for row in rows)
            pushed += len(rows)

    def load_snapshot(self, snapshot: Mapping[str, Any]) -> int:
        """Replace the offline copy of long-term knowledge with a server snapshot."""
        nodes = [
            (
                node["id"],
                node["label"],
                node.get("type"),
                node.get("description"),
                _blob(hash_embed(node.get("description") or node["label"], self._dim)),
            )
            for node in snapshot.get("nodes", [])
        ]
        with self._lock:
            with self._db:
                self._db.execute("DELETE FROM snapshot_nodes")
                self._db.executemany(
                    "INSERT OR REPLACE INTO snapshot_nodes (id, label, type, description, embedding) VALUES (?, ?, ?, ?, ?)",
                    nodes,
                )
        return len(nodes)

    def pull_snapshot(self, transport: Transport, agent_id: str) -> int:
        return self.load_snapshot(transport.get(f"/api/v1/agents/{agent_id}/snapshot"))
