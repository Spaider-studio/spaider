"""Runs against fake rclpy objects. Real ROS 2 has not been exercised."""
from __future__ import annotations

import json
from types import SimpleNamespace

import pytest

from spaider_edge import EdgeMemory
from spaider_ros2 import MemoryBridge, TopicMapping, default_location


class FakeLogger:
    def __init__(self):
        self.warnings = []

    def warning(self, text):
        self.warnings.append(text)


class FakePublisher:
    def __init__(self):
        self.published = []

    def publish(self, message):
        self.published.append(message)


class FakeNode:
    def __init__(self):
        self.callbacks, self.publishers, self.timers, self.logger = {}, {}, [], FakeLogger()

    def create_subscription(self, msg_type, topic, callback, qos):
        self.callbacks[topic] = callback
        return object()

    def create_publisher(self, msg_type, topic, qos):
        self.publishers[topic] = FakePublisher()
        return self.publishers[topic]

    def create_timer(self, period, callback):
        self.timers.append((period, callback))

    def get_logger(self):
        return self.logger


class String:
    def __init__(self, data=""):
        self.data = data


@pytest.fixture
def setup():
    node, memory = FakeNode(), EdgeMemory()
    yield node, memory
    memory.close()


def test_a_message_becomes_an_episode(setup):
    node, memory = setup
    MemoryBridge(node, memory, [TopicMapping("/events", String, kind="event")], session_id="run1")
    node.callbacks["/events"](String("door opened"))
    item = memory.recall("door opened")[0]
    assert item["kind"] == "event" and item["session_id"] == "run1" and item["source"] == "/events"


def test_empty_text_is_skipped(setup):
    node, memory = setup
    MemoryBridge(node, memory, [TopicMapping("/events", String)])
    node.callbacks["/events"](String("  "))
    assert memory.count() == 0


def test_pose_supplies_the_location():
    position = SimpleNamespace(x=1.0, y=2.0, z=3.0)
    stamped = SimpleNamespace(pose=SimpleNamespace(position=position))
    odometry = SimpleNamespace(pose=SimpleNamespace(pose=SimpleNamespace(position=position)))
    assert default_location(stamped) == {"x": 1.0, "y": 2.0, "z": 3.0} == default_location(odometry)
    assert default_location(String("x")) is None


def test_stamped_message_is_stored_once(setup):
    node, memory = setup
    MemoryBridge(node, memory, [TopicMapping("/events", String)])
    message = String("seen")
    message.header = SimpleNamespace(stamp=SimpleNamespace(sec=5, nanosec=7))
    node.callbacks["/events"](message)
    node.callbacks["/events"](message)
    assert memory.count() == 1


def test_a_busy_topic_is_rate_limited(setup):
    node, memory = setup
    now = [0.0]
    MemoryBridge(node, memory, [TopicMapping("/fast", String, min_interval_s=1.0)], clock=lambda: now[0])
    for time_s in (0.0, 0.5, 1.2):
        now[0] = time_s
        node.callbacks["/fast"](String(f"tick {time_s}"))
    assert memory.count() == 2


def test_a_bad_message_is_logged_not_raised(setup):
    node, memory = setup

    def broken(_message):
        raise ValueError("cannot convert")

    MemoryBridge(node, memory, [TopicMapping("/events", String, to_text=broken)])
    node.callbacks["/events"](String("x"))
    assert "cannot convert" in node.logger.warnings[0]


def test_recall_replies_on_the_result_topic(setup):
    node, memory = setup
    MemoryBridge(node, memory, [], string_type=String)
    memory.remember("charger in bay four")
    node.callbacks["/spaider/recall"](String("charger"))
    reply = json.loads(node.publishers["/spaider/recall_result"].published[0].data)
    assert reply[0]["text"] == "charger in bay four"


def test_sync_runs_on_a_timer_when_configured(setup):
    node, memory = setup

    class Transport:
        calls = 0

        def post(self, path, body):
            Transport.calls += 1
            return {}

    MemoryBridge(node, memory, [], transport=Transport(), agent_id="kb", sync_period_s=5.0)
    memory.remember("event")
    assert node.timers[0][0] == 5.0
    node.timers[0][1]()
    assert Transport.calls == 1 and memory.count(pending_only=True) == 0


def test_no_timer_without_a_server(setup):
    node, memory = setup
    MemoryBridge(node, memory, [])
    assert node.timers == []
