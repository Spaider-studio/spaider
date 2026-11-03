"""Snapshots: one bounded observation of a source (window or screen) with
provenance, UIA structure, optional image and the coordinate transform.
"""

from __future__ import annotations

import base64
import logging
import time
import uuid
from dataclasses import dataclass, field
from typing import Optional

from .capture import Frame, PreparedImage, capture_screen, capture_window, frame_hash, prepare_for_model, preview_png
from .uia import UiaProvider, UiTree
from .windows import MonitorInfo, Rect, WindowSource, monitors, refresh_window

logger = logging.getLogger(__name__)


@dataclass
class Snapshot:
    snapshot_id: str
    session_id: str
    captured_at: float  # monotonic
    captured_at_iso: str
    source_kind: str  # window | screen
    target: dict
    quality: str
    error: Optional[str]
    tree: UiTree
    frame: Optional[Frame]
    prepared: Optional[PreparedImage]
    frame_hash: Optional[str]
    transform: dict = field(default_factory=dict)

    @property
    def age_ms(self) -> int:
        return int((time.monotonic() - self.captured_at) * 1000)

    def to_api(self, *, include_image: bool = False, preview: bool = False) -> dict:
        image = None
        if self.frame is not None and self.frame.quality == "ok":
            image = {
                "width": self.frame.image.width,
                "height": self.frame.image.height,
                "bytes": len(self.prepared.png) if self.prepared else 0,
                "png_base64": (base64.b64encode(self.prepared.png).decode("ascii") if include_image and self.prepared else None),
                "preview_png_base64": (base64.b64encode(preview_png(self.frame)).decode("ascii") if preview else None),
            }
        return {
            "snapshot_id": self.snapshot_id,
            "session_id": self.session_id,
            "captured_at": self.captured_at_iso,
            "age_ms": self.age_ms,
            "source_kind": self.source_kind,
            "target": self.target,
            "quality": self.quality,
            "error": self.error,
            "text_excerpt": self.tree.text_excerpt,
            "text_chars": len(self.tree.text_excerpt),
            "elements": [e.to_dict() for e in self.tree.elements],
            "elements_total": self.tree.total_seen,
            "truncated": self.tree.truncated or bool(self.tree.error),
            "image": image,
            "transform": self.transform,
        }

    def context_text(self, max_elements: int = 60, max_chars: int = 3500) -> str:
        """Compact structured description for the model (German labels)."""
        lines = [f"Quelle: {self.target.get('title') or self.target.get('name') or '?'} ({self.target.get('process') or self.source_kind}), Zustand {self.quality}"]
        if self.tree.error:
            lines.append(f"Struktur nicht lesbar: {self.tree.error}")
        shown = 0
        for e in self.tree.elements:
            if shown >= max_elements:
                lines.append("… (weitere Elemente gekürzt)")
                break
            if not e.name and not e.value:
                continue
            val = f" = {e.value[:120]!r}" if e.value else ""
            foc = " [Fokus]" if e.focused else ""
            dis = " [deaktiviert]" if not e.enabled else ""
            lines.append(f"- {e.id} {e.role}: {e.name[:120]!r}{val}{foc}{dis} @({e.rect.x},{e.rect.y},{e.rect.w}x{e.rect.h})")
            shown += 1
        if self.tree.truncated:
            lines.append("(Struktur gekürzt)")
        text = "\n".join(lines)
        return text[:max_chars] + ("\n…" if len(text) > max_chars else "")


class SnapshotTaker:
    def __init__(self, uia: UiaProvider):
        self.uia = uia

    def take(
        self,
        session_id: str,
        source: WindowSource | MonitorInfo,
        *,
        want_image: bool,
        want_structure: bool = True,
        max_elements: int = 250,
        max_side: int = 1280,
        prev_hash: Optional[str] = None,
    ) -> Snapshot:
        now = time.monotonic()
        iso = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
        sid = f"snap_{uuid.uuid4().hex[:12]}"
        if isinstance(source, WindowSource):
            fresh = refresh_window(source)
            if fresh is None:
                return Snapshot(sid, session_id, now, iso, "window", source.to_dict(), "lost", "Fenster nicht mehr vorhanden", UiTree(), None, None, None)
            target = fresh.to_dict()
            tree = self.uia.read_window(fresh.hwnd, max_elements=max_elements) if want_structure else UiTree()
            frame = capture_window(fresh) if want_image else None
            scale = 1.0
            for m in monitors():
                if m.id == fresh.monitor_id:
                    scale = m.scale
            target["scale"] = scale
        else:
            target = {"name": source.name, "monitor_id": source.id, "rect": source.rect.to_dict(), "scale": source.scale, "process": None, "title": source.name}
            tree = UiTree()
            frame = capture_screen(source.id) if want_image else None
        quality = frame.quality if frame is not None else ("ok" if not tree.error else "error")
        error = frame.error if frame is not None and frame.error else None
        prepared = None
        fhash = None
        if frame is not None and frame.quality == "ok":
            fhash = frame_hash(frame.image)
            prepared = prepare_for_model(frame, max_side=max_side)
        transform = {}
        if prepared is not None:
            transform = {"origin_x": prepared.origin_x, "origin_y": prepared.origin_y, "scale": prepared.scale, "model_width": prepared.width, "model_height": prepared.height}
        elif frame is not None:
            transform = {"origin_x": frame.origin_x, "origin_y": frame.origin_y, "scale": 1.0}
        return Snapshot(sid, session_id, now, iso, "window" if isinstance(source, WindowSource) else "screen", target, quality, error, tree, frame, prepared, fhash, transform)


def rect_from_element(el: dict) -> Rect:
    r = el["rect"]
    return Rect(int(r["x"]), int(r["y"]), int(r["w"]), int(r["h"]))
