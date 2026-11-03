"""Bounded UI Automation reads through pywinauto (backend="uia").

All UIA/COM work runs on ONE long-lived worker thread (COM apartment rules;
pywinauto's comtypes wrappers are not thread-safe). Jobs are submitted with a
timeout; a hanging provider makes the call return an error and marks the
worker as stuck (a fresh worker is started for the next job; the stuck thread
is left to die with the process - it is a daemon thread).

Reads are bounded: max depth, max elements, max text per element and a time
budget. The result says ``truncated`` when any bound was hit.
"""

from __future__ import annotations

import logging
import queue
import sys
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Callable, Optional

from .windows import IS_WIN, Rect

logger = logging.getLogger(__name__)

_INTERACTIVE = {"Button", "Edit", "ComboBox", "CheckBox", "RadioButton", "Hyperlink", "MenuItem", "TabItem", "ListItem", "TreeItem", "Slider", "Spinner", "SplitButton", "Document", "Text", "Custom"}


@dataclass
class UiElement:
    id: str  # stable within one snapshot: path of child indices
    role: str
    name: str
    value: Optional[str]
    rect: Rect
    enabled: bool
    focused: bool
    path: str
    automation_id: str = ""
    class_name: str = ""
    runtime_id: tuple = ()

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "role": self.role,
            "name": self.name,
            "value": self.value,
            "rect": self.rect.to_dict(),
            "enabled": self.enabled,
            "focused": self.focused,
            "path": self.path,
            "automation_id": self.automation_id,
        }


@dataclass
class UiTree:
    elements: list[UiElement] = field(default_factory=list)
    total_seen: int = 0
    truncated: bool = False
    text_excerpt: str = ""
    error: Optional[str] = None
    duration_ms: int = 0
    focused_path: Optional[str] = None


class _Worker:
    def __init__(self) -> None:
        self.q: "queue.Queue[tuple[Callable, dict, queue.Queue]]" = queue.Queue()
        self.thread = threading.Thread(target=self._run, name="uia-worker", daemon=True)
        self.stuck = False
        self.thread.start()

    def _run(self) -> None:
        try:
            import pywinauto  # noqa: F401  (initialises COM as STA on this thread)
        except Exception:  # noqa: BLE001
            pass
        while True:
            fn, kwargs, reply = self.q.get()
            try:
                reply.put(("ok", fn(**kwargs)))
            except Exception as exc:  # noqa: BLE001
                reply.put(("error", exc))


class UiaProvider:
    """Thread-confined entry point used by snapshots and the Windows tools."""

    def __init__(self) -> None:
        self._worker: Optional[_Worker] = None
        self._lock = threading.Lock()
        self.available = IS_WIN
        self.last_error: Optional[str] = None
        self.warm = False
        self.warmup_ms: Optional[int] = None

    def warmup(self) -> None:
        """Import pywinauto on the worker thread and touch the desktop once (generates comtypes wrappers)."""
        if not self.available or self.warm:
            return
        t0 = time.monotonic()
        try:
            self.call(_warmup, timeout=120.0)
            self.warm = True
            self.warmup_ms = int((time.monotonic() - t0) * 1000)
            logger.info("uia warm-up done in %d ms", self.warmup_ms)
        except Exception as exc:  # noqa: BLE001
            self.last_error = f"UIA-Aufwärmen fehlgeschlagen: {exc}"[:300]
            logger.warning(self.last_error)

    def _get_worker(self) -> _Worker:
        with self._lock:
            if self._worker is None or self._worker.stuck or not self._worker.thread.is_alive():
                self._worker = _Worker()
            return self._worker

    def call(self, fn: Callable, timeout: float = 8.0, **kwargs) -> Any:
        if not self.available:
            raise RuntimeError("UI Automation ist nur unter Windows verfügbar.")
        w = self._get_worker()
        reply: queue.Queue = queue.Queue()
        w.q.put((fn, kwargs, reply))
        try:
            status, payload = reply.get(timeout=timeout)
        except queue.Empty:
            w.stuck = True
            self.last_error = f"UIA-Anbieter antwortet nicht ({timeout:.0f} s)"
            raise TimeoutError(self.last_error)
        if status == "error":
            raise payload
        return payload

    # ---- reads -----------------------------------------------------------------
    def read_window(self, hwnd: int, *, max_depth: int = 12, max_elements: int = 250, max_text: int = 4000, time_budget_s: float = 4.0, timeout: float = 10.0) -> UiTree:
        if not self.warm:
            timeout = max(timeout, 60.0)  # first use may still be generating wrappers
        try:
            tree = self.call(_read_window, timeout=timeout, hwnd=hwnd, max_depth=max_depth, max_elements=max_elements, max_text=max_text, time_budget_s=time_budget_s)
            self.warm = True
            return tree
        except TimeoutError as exc:
            return UiTree(error=str(exc), truncated=True)
        except Exception as exc:  # noqa: BLE001
            return UiTree(error=f"UIA-Fehler: {exc}"[:300])

    def find_element(self, hwnd: int, path: str, timeout: float = 8.0):
        """Re-resolve one element by its snapshot path (fresh wrapper for actions)."""
        return self.call(_find_by_path, timeout=timeout, hwnd=hwnd, path=path)


# ---------------------------------------------------------------- worker-side functions (run on the UIA thread)
def _wrap(hwnd: int):
    from pywinauto import Desktop

    return Desktop(backend="uia").window(handle=hwnd)


def _elem_rect(info) -> Rect:
    try:
        r = info.rectangle
        return Rect(int(r.left), int(r.top), int(r.width()), int(r.height()))
    except Exception:  # noqa: BLE001
        return Rect(0, 0, 0, 0)


def _elem_value(info, role: str) -> Optional[str]:
    if role not in ("Edit", "Document", "ComboBox", "Spinner", "Slider", "Text", "CheckBox", "RadioButton"):
        return None
    try:
        el = info.element  # IUIAutomationElement
        # ValuePattern (10002) first, then LegacyIAccessible value
        pat = el.GetCurrentPattern(10002)
        if pat:
            from comtypes import POINTER  # noqa: F401
            from comtypes.gen import UIAutomationClient as uia

            vp = pat.QueryInterface(uia.IUIAutomationValuePattern)
            return str(vp.CurrentValue)
    except Exception:  # noqa: BLE001
        pass
    try:
        if role in ("CheckBox", "RadioButton"):
            el = info.element
            pat = el.GetCurrentPattern(10010)  # TogglePattern
            if pat:
                from comtypes.gen import UIAutomationClient as uia

                tp = pat.QueryInterface(uia.IUIAutomationTogglePattern)
                return {0: "off", 1: "on", 2: "indeterminate"}.get(int(tp.CurrentToggleState), None)
    except Exception:  # noqa: BLE001
        pass
    return None


def _read_window(hwnd: int, max_depth: int, max_elements: int, max_text: int, time_budget_s: float) -> UiTree:
    t0 = time.monotonic()
    tree = UiTree()
    try:
        root = _wrap(hwnd)
        root_info = root.element_info
    except Exception as exc:  # noqa: BLE001
        tree.error = f"Fenster nicht lesbar: {exc}"[:200]
        return tree
    texts: list[str] = []
    text_chars = 0
    stack: list[tuple[Any, str, int]] = [(root_info, "0", 0)]
    while stack:
        if time.monotonic() - t0 > time_budget_s:
            tree.truncated = True
            break
        info, path, depth = stack.pop(0)
        tree.total_seen += 1
        try:
            role = info.control_type or "Unknown"
            name = (info.name or "")[:200]
            rect = _elem_rect(info)
            enabled = bool(getattr(info, "enabled", True))
            focused = False
            try:
                focused = bool(info.element.CurrentHasKeyboardFocus)
            except Exception:  # noqa: BLE001
                pass
            visible = bool(getattr(info, "visible", True))
        except Exception:  # noqa: BLE001
            continue
        if not visible and depth > 0:
            continue
        value = _elem_value(info, role)
        interesting = role in _INTERACTIVE or bool(name.strip()) or depth == 0
        if interesting and (rect.w > 0 and rect.h > 0 or depth == 0):
            if len(tree.elements) < max_elements:
                el = UiElement(
                    id=f"e{len(tree.elements)}",
                    role=role,
                    name=name,
                    value=(value[:500] if value else value),
                    rect=rect,
                    enabled=enabled,
                    focused=focused,
                    path=path,
                    automation_id=str(getattr(info, "automation_id", "") or "")[:80],
                    class_name=str(getattr(info, "class_name", "") or "")[:60],
                )
                tree.elements.append(el)
                if focused:
                    tree.focused_path = path
                chunk = name if role not in ("Edit", "Document") else (value or name)
                if chunk and text_chars < max_text:
                    piece = chunk.strip()
                    if piece and piece not in texts[-3:]:
                        texts.append(piece)
                        text_chars += len(piece)
            else:
                tree.truncated = True
        if depth < max_depth:
            try:
                children = info.children()
            except Exception:  # noqa: BLE001
                children = []
            for i, ch in enumerate(children[:200]):
                stack.append((ch, f"{path}.{i}", depth + 1))
            if len(children) > 200:
                tree.truncated = True
        else:
            tree.truncated = True if depth == max_depth and info.children() else tree.truncated
    tree.text_excerpt = "\n".join(texts)[:max_text]
    tree.duration_ms = int((time.monotonic() - t0) * 1000)
    return tree


def _warmup():
    from pywinauto import Desktop

    return len(Desktop(backend="uia").windows())


def _find_by_path(hwnd: int, path: str):
    root = _wrap(hwnd)
    info = root.element_info
    for idx in [int(p) for p in path.split(".")[1:]]:
        children = info.children()
        if idx >= len(children):
            raise LookupError("Element nicht mehr vorhanden")
        info = children[idx]
    from pywinauto.controls.uiawrapper import UIAWrapper

    return UIAWrapper(info)


def configure_comtypes_cache(cache_dir) -> None:
    """Frozen builds: comtypes writes generated typelib wrappers next to its package,
    which is read-only inside the PyInstaller bundle. Point it at a writable folder
    under the portable data directory before pywinauto is imported anywhere."""
    try:
        from pathlib import Path

        gen = Path(cache_dir) / "comtypes_gen"
        gen.mkdir(parents=True, exist_ok=True)
        import comtypes
        import comtypes.client

        comtypes.client.gen_dir = str(gen)
        try:
            import comtypes.gen as cgen  # noqa: F401

            if str(gen) not in cgen.__path__:
                cgen.__path__.append(str(gen))
        except ImportError:
            import types

            mod = types.ModuleType("comtypes.gen")
            mod.__path__ = [str(gen)]  # type: ignore[attr-defined]
            sys.modules["comtypes.gen"] = mod
            comtypes.gen = mod  # type: ignore[attr-defined]
    except Exception as exc:  # noqa: BLE001
        logger.warning("comtypes cache setup failed: %s", exc)


def available() -> bool:
    if not IS_WIN:
        return False
    try:
        import pywinauto  # noqa: F401

        return True
    except Exception:  # noqa: BLE001
        return False


if sys.platform != "win32":  # pragma: no cover
    pass
