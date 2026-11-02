"""Typed activity events (docs/portable/ACTIVITY_EVENTS.md).

An ``ActivityTracker`` belongs to one request (one assistant message). It
numbers events, timestamps them relative to the request start, keeps a bounded
in-memory history for the "Details" view and forwards every event to the
request's SSE stream and to the global ``ActivityBus`` (long-lived
``GET /api/activity`` subscribers).
"""

from __future__ import annotations

import asyncio
import json
import time
import uuid
from collections import deque
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Callable, Optional

MAX_EVENTS_PER_REQUEST = 200
MAX_REQUEST_HISTORIES = 50

PHASES = (
    "queued",
    "model_loading",
    "memory_search",
    "memory_reconcile",
    "prompt_build",
    "prompt_processing",
    "generating",
    "answer_done",
    "saved",
    "postprocess_pending",
    "postprocess_running",
    "postprocess_done",
    "failed",
)
STATES = ("started", "progress", "done", "failed", "cancelled")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


@dataclass
class ActivityEvent:
    request_id: str
    seq: int
    phase: str
    state: str
    t_ms: int
    at: str
    message_de: str = ""
    counters: dict = field(default_factory=dict)
    source_ids: list[str] = field(default_factory=list)
    conversation_id: Optional[str] = None

    def to_dict(self) -> dict:
        d = asdict(self)
        if d["conversation_id"] is None:
            d.pop("conversation_id")
        return d


class ActivityBus:
    """Global fan-out for events that outlive a chat stream (post-processing,
    scheduler and runtime state). Subscribers get asyncio queues."""

    def __init__(self) -> None:
        self._subs: set[asyncio.Queue] = set()
        self._histories: dict[str, deque] = {}
        self._order: deque = deque()

    def subscribe(self) -> asyncio.Queue:
        q: asyncio.Queue = asyncio.Queue(maxsize=500)
        self._subs.add(q)
        return q

    def unsubscribe(self, q: asyncio.Queue) -> None:
        self._subs.discard(q)

    def publish(self, kind: str, payload: dict) -> None:
        for q in list(self._subs):
            try:
                q.put_nowait((kind, payload))
            except asyncio.QueueFull:
                pass

    # ---- per-request histories (for "Details") --------------------------
    def remember(self, event: ActivityEvent) -> None:
        hist = self._histories.get(event.request_id)
        if hist is None:
            hist = deque(maxlen=MAX_EVENTS_PER_REQUEST)
            self._histories[event.request_id] = hist
            self._order.append(event.request_id)
            while len(self._order) > MAX_REQUEST_HISTORIES:
                old = self._order.popleft()
                self._histories.pop(old, None)
        hist.append(event)

    def history(self, request_id: str) -> list[dict]:
        return [e.to_dict() for e in self._histories.get(request_id, [])]


class ActivityTracker:
    def __init__(
        self,
        request_id: str,
        bus: Optional[ActivityBus] = None,
        *,
        conversation_id: Optional[str] = None,
        emit: Optional[Callable[[ActivityEvent], None]] = None,
    ):
        self.request_id = request_id
        self.bus = bus
        self.conversation_id = conversation_id
        self._emit = emit
        self._seq = 0
        self._t0 = time.monotonic()
        self._phase_started: dict[str, float] = {}
        self.timings_ms: dict[str, int] = {}
        self._last_progress: dict[str, float] = {}
        self.cancelled = False

    @property
    def elapsed_ms(self) -> int:
        return int((time.monotonic() - self._t0) * 1000)

    def _make(
        self, phase: str, state: str, message_de: str, counters: Optional[dict], source_ids: Optional[list[str]]
    ) -> ActivityEvent:
        self._seq += 1
        return ActivityEvent(
            request_id=self.request_id,
            seq=self._seq,
            phase=phase,
            state=state,
            t_ms=self.elapsed_ms,
            at=utcnow(),
            message_de=message_de,
            counters=dict(counters or {}),
            source_ids=list(source_ids or []),
            conversation_id=self.conversation_id,
        )

    def _dispatch(self, ev: ActivityEvent) -> ActivityEvent:
        if self.bus is not None:
            self.bus.remember(ev)
        if self._emit is not None:
            self._emit(ev)
        return ev

    def start(self, phase: str, message_de: str, counters: Optional[dict] = None) -> ActivityEvent:
        self._phase_started[phase] = time.monotonic()
        return self._dispatch(self._make(phase, "started", message_de, counters, None))

    def progress(
        self, phase: str, message_de: str, counters: Optional[dict] = None, *, min_interval: float = 0.25
    ) -> Optional[ActivityEvent]:
        """Rate-limited progress event (at most one per ``min_interval``)."""
        now = time.monotonic()
        if now - self._last_progress.get(phase, 0.0) < min_interval:
            return None
        self._last_progress[phase] = now
        return self._dispatch(self._make(phase, "progress", message_de, counters, None))

    def done(
        self, phase: str, message_de: str, counters: Optional[dict] = None, source_ids: Optional[list[str]] = None
    ) -> ActivityEvent:
        started = self._phase_started.pop(phase, None)
        if started is not None:
            self.timings_ms[phase + "_ms"] = int((time.monotonic() - started) * 1000)
        return self._dispatch(self._make(phase, "done", message_de, counters, source_ids))

    def fail(self, phase: str, message_de: str, counters: Optional[dict] = None) -> ActivityEvent:
        started = self._phase_started.pop(phase, None)
        if started is not None:
            self.timings_ms[phase + "_ms"] = int((time.monotonic() - started) * 1000)
        return self._dispatch(self._make(phase, "failed", message_de, counters, None))

    def cancel(self, phase: str, message_de: str, counters: Optional[dict] = None) -> ActivityEvent:
        self.cancelled = True
        started = self._phase_started.pop(phase, None)
        if started is not None:
            self.timings_ms[phase + "_ms"] = int((time.monotonic() - started) * 1000)
        return self._dispatch(self._make(phase, "cancelled", message_de, counters, None))

    def summary(self) -> dict:
        return {**self.timings_ms, "total_ms": self.elapsed_ms}


def sse_activity(ev: ActivityEvent) -> str:
    return f"event: activity\ndata: {json.dumps(ev.to_dict(), ensure_ascii=False)}\n\n"


def new_request_id() -> str:
    return str(uuid.uuid4())
