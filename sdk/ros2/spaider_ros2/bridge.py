"""Connects ROS 2 topics to on-device memory."""
from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from spaider_edge import EdgeMemory, HttpTransport

_QUEUE_DEPTH = 10


def default_to_text(message: Any) -> str:
    data = getattr(message, "data", None)
    return data if isinstance(data, str) else str(message)


def default_location(message: Any) -> Optional[Dict[str, float]]:
    """Reads a Pose, PoseStamped or Odometry-shaped message if there is one."""
    pose = getattr(message, "pose", None)
    pose = getattr(pose, "pose", pose)
    position = getattr(pose, "position", None)
    if position is None:
        return None
    return {"x": float(position.x), "y": float(position.y), "z": float(position.z)}


def _stamp_key(topic: str, message: Any) -> Optional[str]:
    stamp = getattr(getattr(message, "header", None), "stamp", None)
    if stamp is None:
        return None
    return f"{topic}:{stamp.sec}.{stamp.nanosec}"


@dataclass
class TopicMapping:
    topic: str
    msg_type: Any
    kind: str = "observation"
    to_text: Callable[[Any], str] = default_to_text
    location: Callable[[Any], Optional[Dict[str, float]]] = default_location
    min_interval_s: float = 0.0


class MemoryBridge:
    def __init__(
        self,
        node: Any,
        memory: EdgeMemory,
        mappings: List[TopicMapping],
        *,
        session_id: Optional[str] = None,
        transport: Any = None,
        agent_id: Optional[str] = None,
        sync_period_s: float = 30.0,
        string_type: Any = None,
        recall_topic: str = "/spaider/recall",
        result_topic: str = "/spaider/recall_result",
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._node = node
        self._memory = memory
        self._session_id = session_id
        self._transport = transport
        self._agent_id = agent_id
        self._clock = clock
        self._last_stored: Dict[str, float] = {}
        self._string_type = string_type
        self._subscriptions = [
            node.create_subscription(
                mapping.msg_type, mapping.topic, self._callback(mapping), _QUEUE_DEPTH
            )
            for mapping in mappings
        ]
        self._publisher = None
        if string_type is not None:
            self._publisher = node.create_publisher(string_type, result_topic, _QUEUE_DEPTH)
            self._subscriptions.append(
                node.create_subscription(string_type, recall_topic, self._on_recall, _QUEUE_DEPTH)
            )
        if transport is not None and agent_id is not None:
            node.create_timer(sync_period_s, self.sync_once)

    def _callback(self, mapping: TopicMapping) -> Callable[[Any], None]:
        def handle(message: Any) -> None:
            self._store(mapping, message)

        return handle

    def _store(self, mapping: TopicMapping, message: Any) -> None:
        now = self._clock()
        last = self._last_stored.get(mapping.topic)
        if last is not None and now - last < mapping.min_interval_s:
            return
        try:
            text = mapping.to_text(message)
            if not text or not text.strip():
                return
            self._memory.remember(
                text,
                kind=mapping.kind,
                session_id=self._session_id,
                source=mapping.topic,
                location=mapping.location(message),
                idempotency_key=_stamp_key(mapping.topic, message),
            )
            self._last_stored[mapping.topic] = now
        except Exception as exc:  # noqa: BLE001 — one bad message must not stop the callback thread
            self._node.get_logger().warning(f"Could not store message from {mapping.topic}: {exc}")

    def _on_recall(self, message: Any) -> None:
        results = self._memory.recall(message.data)
        reply = self._string_type()
        reply.data = json.dumps(results, default=str)
        self._publisher.publish(reply)

    def sync_once(self) -> Dict[str, int]:
        return self._memory.sync(self._transport, self._agent_id)


def main() -> None:
    """Bridge a comma-separated list of String topics. Needs a ROS 2 environment."""
    import rclpy
    from std_msgs.msg import String

    rclpy.init()
    node = rclpy.create_node("spaider_memory_bridge")
    topics = [topic for topic in os.environ.get("SPAIDER_TOPICS", "/events").split(",") if topic]
    url, key, agent = (os.environ.get(name) for name in ("SPAIDER_URL", "SPAIDER_API_KEY", "SPAIDER_AGENT_ID"))
    transport = HttpTransport(url, key) if url and key else None
    MemoryBridge(
        node,
        EdgeMemory(os.environ.get("SPAIDER_DB", "spaider_edge.db")),
        [TopicMapping(topic, String) for topic in topics],
        transport=transport,
        agent_id=agent,
        string_type=String,
    )
    try:
        rclpy.spin(node)
    finally:
        node.destroy_node()
        rclpy.shutdown()
