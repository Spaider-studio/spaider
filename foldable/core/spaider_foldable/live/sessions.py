"""Live session manager.

A session binds exactly one source. Snapshots are taken on demand (question,
preview request, agent step); there is no background capture loop. Frames are
kept in memory only (last two), never written to disk.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Callable, Optional

from ..activity import ActivityTracker
from ..observe import windows as W
from ..observe.snapshot import Snapshot, SnapshotTaker
from ..observe.uia import UiaProvider

logger = logging.getLogger(__name__)


@dataclass
class Observation:
    """What a chat turn gets from a live session."""

    snapshot: Snapshot
    text: str  # structured description for the prompt
    image_data_url: Optional[str]  # None when no vision or not wanted
    used_image: bool
    used_structure: bool

    def to_event(self) -> dict:
        s = self.snapshot
        return {
            "snapshot_id": s.snapshot_id,
            "source_kind": s.source_kind,
            "quality": s.quality,
            "used_image": self.used_image,
            "used_structure": self.used_structure,
            "elements": len(s.tree.elements),
            "text_chars": len(s.tree.text_excerpt),
            "age_ms": s.age_ms,
        }


@dataclass
class LiveSession:
    id: str
    source: W.WindowSource | W.MonitorInfo
    conversation_id: str
    created_at: str
    state: str = "active"  # active | lost | ended
    snapshots: list[Snapshot] = field(default_factory=list)
    notes_de: list[str] = field(default_factory=list)
    asks: int = 0

    @property
    def capture_mode(self) -> str:
        return "window" if isinstance(self.source, W.WindowSource) else "screen"

    @property
    def source_id(self) -> str:
        return self.source.id

    def to_api(self, vision_available: bool) -> dict:
        last = self.snapshots[-1] if self.snapshots else None
        return {
            "id": self.id,
            "source": self.source.to_dict() if isinstance(self.source, W.WindowSource) else self.source.to_source(),
            "capture_mode": self.capture_mode,
            "state": self.state,
            "conversation_id": self.conversation_id,
            "created_at": self.created_at,
            "last_snapshot": (
                {"snapshot_id": last.snapshot_id, "captured_at": last.captured_at_iso, "age_ms": last.age_ms, "quality": last.quality}
                if last
                else None
            ),
            "vision_available": vision_available,
            "notes_de": list(self.notes_de),
        }


class SourceNotFound(Exception):
    pass


class SourceIsSelf(Exception):
    pass


class LiveUnavailable(Exception):
    pass


class LiveManager:
    def __init__(self, *, uia: UiaProvider, conversations, vision_available: Callable[[], bool], on_change: Optional[Callable[[dict], None]] = None, max_side: Callable[[], int] = lambda: 1280):
        self.uia = uia
        self.taker = SnapshotTaker(uia)
        self.conversations = conversations
        self.vision_available = vision_available
        self.on_change = on_change
        self.max_side = max_side
        self.sessions: dict[str, LiveSession] = {}
        self._lock = asyncio.Lock()

    # ---- sources ---------------------------------------------------------------
    def list_sources(self) -> dict:
        wins = W.list_windows()
        fg = W.foreground_window()
        return {
            "windows": [w.to_dict() for w in wins],
            "screens": [m.to_source() for m in W.monitors()],
            "foreground_id": fg.id if fg else None,
            "desktop_interactive": W.desktop_interactive(),
        }

    def foreground(self) -> dict:
        fg = W.foreground_window()
        return {"source": fg.to_dict() if fg else None, "captured_at": _iso()}

    def resolve_source(self, source_id: str) -> W.WindowSource | W.MonitorInfo:
        kind, a, b = W.parse_source_id(source_id)
        if kind == "screen":
            for m in W.monitors():
                if m.id == source_id:
                    return m
            raise SourceNotFound(source_id)
        src = W.describe_window(a)
        if src is None or src.pid != b:
            raise SourceNotFound(source_id)
        if src.is_self:
            raise SourceIsSelf(source_id)
        return src

    # ---- sessions ---------------------------------------------------------------
    def create(self, source_id: str, conversation_id: Optional[str]) -> LiveSession:
        if not W.desktop_interactive():
            raise LiveUnavailable("Kein interaktiver Desktop (gesperrt oder Sitzung ohne Anzeige).")
        source = self.resolve_source(source_id)
        if conversation_id is None:
            title = source.title if isinstance(source, W.WindowSource) else source.name
            conv = self.conversations.create(f"Live: {title[:50]}")
            conversation_id = conv["id"]
        sess = LiveSession(id=f"live_{uuid.uuid4().hex[:10]}", source=source, conversation_id=conversation_id, created_at=_iso())
        if isinstance(source, W.MonitorInfo):
            sess.notes_de.append("Vollbildquelle: Alles auf diesem Bildschirm kann aufgenommen werden; eigene SpAIder-Fenster sind ausgenommen.")
        if not self.vision_available():
            sess.notes_de.append("Kein Bildmodell aktiv: Fragen nutzen nur die lesbare Struktur (UIA/Text), keine Bildinhalte.")
        self.sessions[sess.id] = sess
        self._emit(sess)
        return sess

    def get(self, session_id: str) -> LiveSession:
        s = self.sessions.get(session_id)
        if s is None:
            raise SourceNotFound(session_id)
        return s

    def change_source(self, session_id: str, source_id: str) -> LiveSession:
        s = self.get(session_id)
        s.source = self.resolve_source(source_id)
        s.state = "active"
        s.snapshots.clear()
        self._emit(s)
        return s

    def end(self, session_id: str) -> None:
        s = self.sessions.pop(session_id, None)
        if s is not None:
            s.state = "ended"
            s.snapshots.clear()
            self._emit(s)

    def end_all(self) -> int:
        n = 0
        for sid in list(self.sessions):
            self.end(sid)
            n += 1
        return n

    # ---- snapshots ---------------------------------------------------------------
    async def snapshot(self, session_id: str, *, want_image: bool, max_elements: int = 250) -> Snapshot:
        s = self.get(session_id)
        snap = await asyncio.to_thread(self.taker.take, s.id, s.source, want_image=want_image, max_elements=max_elements, max_side=self.max_side())
        s.snapshots.append(snap)
        del s.snapshots[:-2]
        if snap.quality == "lost":
            s.state = "lost"
            self._emit(s)
        elif s.state == "lost":
            s.state = "active"
            self._emit(s)
        return snap

    async def observe_for_question(self, session_id: str, tracker: ActivityTracker, *, use_image: str = "auto") -> Observation:
        """Snapshot + model-ready observation; emits capture/observe/image_encode phases."""
        s = self.get(session_id)
        vision = self.vision_available()
        want_image = use_image == "always" or (use_image == "auto" and vision)
        if want_image and not vision:
            raise LiveUnavailable("Kein Bildmodell aktiv.")
        tracker.start("capture", "Quelle wird aufgenommen")
        t0 = time.monotonic()
        snap = await self.snapshot(session_id, want_image=want_image)
        tracker.done("capture", f"Aufnahme fertig ({snap.quality})", {"capture_ms": int((time.monotonic() - t0) * 1000), "quality": snap.quality})
        tracker.start("observe", "Struktur wird gelesen")
        tracker.done(
            "observe",
            f"{len(snap.tree.elements)} Elemente, {len(snap.tree.text_excerpt)} Zeichen" + (" (gekürzt)" if snap.tree.truncated else ""),
            {"elements": len(snap.tree.elements), "text_chars": len(snap.tree.text_excerpt), "uia_ms": snap.tree.duration_ms},
        )
        image_url = None
        if want_image and snap.prepared is not None:
            tracker.start("image_encode", "Bild wird vorbereitet")
            image_url = snap.prepared.to_data_url()
            tracker.done("image_encode", f"Bild {snap.prepared.width}×{snap.prepared.height}", {"image_w": snap.prepared.width, "image_h": snap.prepared.height, "image_bytes": len(snap.prepared.png)})
        s.asks += 1
        return Observation(snapshot=snap, text=snap.context_text(), image_data_url=image_url, used_image=image_url is not None, used_structure=bool(snap.tree.elements))

    def _emit(self, s: LiveSession) -> None:
        if self.on_change:
            try:
                self.on_change(s.to_api(self.vision_available()))
            except Exception:  # noqa: BLE001
                pass


def _iso() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
