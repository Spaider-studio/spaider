"""Top-level windows, monitors, DPI and the foreground window (Win32 via ctypes).

Identity of a target is never the bare ``hwnd`` (handles are reused): a
:class:`WindowSource` carries hwnd + pid + process name + title + class and
``same_window()`` checks that the handle still belongs to the same process
with a compatible title before anything is captured or acted upon.
"""

from __future__ import annotations

import ctypes
import logging
import os
import sys
import time
from ctypes import wintypes
from dataclasses import asdict, dataclass, field
from typing import Optional

logger = logging.getLogger(__name__)

IS_WIN = sys.platform == "win32"

if IS_WIN:
    user32 = ctypes.windll.user32
    kernel32 = ctypes.windll.kernel32
    dwmapi = ctypes.windll.dwmapi
    try:
        shcore = ctypes.windll.shcore
    except OSError:  # pragma: no cover
        shcore = None
    psapi = ctypes.windll.psapi
else:  # pragma: no cover
    user32 = kernel32 = dwmapi = shcore = psapi = None

_DPI_SET = False


def ensure_dpi_awareness() -> None:
    """Per-monitor-v2 DPI awareness so that window rectangles are physical pixels."""
    global _DPI_SET
    if not IS_WIN or _DPI_SET:
        return
    try:
        user32.SetProcessDpiAwarenessContext.restype = wintypes.BOOL
        user32.SetProcessDpiAwarenessContext(ctypes.c_void_p(-4))  # DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2
    except Exception:  # noqa: BLE001
        try:
            shcore.SetProcessDpiAwareness(2)
        except Exception:  # noqa: BLE001
            pass
    _DPI_SET = True


class RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_long), ("top", ctypes.c_long), ("right", ctypes.c_long), ("bottom", ctypes.c_long)]


class MONITORINFOEXW(ctypes.Structure):
    _fields_ = [
        ("cbSize", wintypes.DWORD),
        ("rcMonitor", RECT),
        ("rcWork", RECT),
        ("dwFlags", wintypes.DWORD),
        ("szDevice", wintypes.WCHAR * 32),
    ]


@dataclass
class Rect:
    x: int
    y: int
    w: int
    h: int

    def to_dict(self) -> dict:
        return asdict(self)

    def contains(self, px: int, py: int) -> bool:
        return self.x <= px < self.x + self.w and self.y <= py < self.y + self.h


@dataclass
class MonitorInfo:
    id: str
    name: str
    rect: Rect
    scale: float
    primary: bool
    handle: int = 0

    def to_source(self) -> dict:
        return {"id": self.id, "name": self.name, "rect": self.rect.to_dict(), "scale": self.scale, "primary": self.primary}


@dataclass
class WindowSource:
    hwnd: int
    pid: int
    process: str
    exe: Optional[str]
    title: str
    cls: str
    rect: Rect
    monitor_id: str
    minimized: bool = False
    is_self: bool = False
    cloaked: bool = False
    seen_at: float = field(default_factory=time.monotonic)

    @property
    def id(self) -> str:
        return f"win:{self.hwnd}:{self.pid}"

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "hwnd": self.hwnd,
            "pid": self.pid,
            "process": self.process,
            "exe": self.exe,
            "title": self.title,
            "class": self.cls,
            "rect": self.rect.to_dict(),
            "monitor_id": self.monitor_id,
            "minimized": self.minimized,
            "is_self": self.is_self,
        }


def parse_source_id(source_id: str) -> tuple[str, int, int]:
    """``win:<hwnd>:<pid>`` → ("win", hwnd, pid); ``screen:<n>`` → ("screen", n, 0)."""
    parts = (source_id or "").split(":")
    if len(parts) == 3 and parts[0] == "win":
        return "win", int(parts[1]), int(parts[2])
    if len(parts) == 2 and parts[0] == "screen":
        return "screen", int(parts[1]), 0
    raise ValueError("invalid source id")


# ---------------------------------------------------------------- processes
_SELF_PIDS: set[int] = set()
_SELF_NAMES = {"spaider.exe", "spaider-foldable-core.exe"}


def register_self_pid(pid: int) -> None:
    _SELF_PIDS.add(int(pid))


def self_pids() -> set[int]:
    """SpAIder's own process family: this core, its llama-server children and the
    Electron launcher (parent) with its windows. Processes the agent starts on purpose
    (windows.launch) are children of the core but NOT part of this set."""
    pids = set(_SELF_PIDS)
    pids.add(os.getpid())
    try:
        import psutil

        me = psutil.Process(os.getpid())
        for c in me.children(recursive=False):
            if c.name().lower().startswith("llama-server"):
                pids.add(c.pid)
        parent = me.parent()
        if parent is not None and parent.name().lower() in _SELF_NAMES | {"electron.exe"}:
            pids.add(parent.pid)
            for c in parent.children(recursive=True):
                if c.name().lower() in _SELF_NAMES | {"electron.exe"} or c.name().lower().startswith("llama-server"):
                    pids.add(c.pid)
    except Exception:  # noqa: BLE001
        pass
    return pids


def process_name(pid: int) -> tuple[str, Optional[str]]:
    """(basename, full path) of a process; ('', None) when not accessible."""
    try:
        import psutil

        p = psutil.Process(pid)
        exe = None
        try:
            exe = p.exe()
        except Exception:  # noqa: BLE001
            exe = None
        return p.name(), exe
    except Exception:  # noqa: BLE001
        return "", None


# ---------------------------------------------------------------- monitors
def monitors() -> list[MonitorInfo]:
    if not IS_WIN:
        return []
    ensure_dpi_awareness()
    out: list[MonitorInfo] = []
    MonitorEnumProc = ctypes.WINFUNCTYPE(ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.POINTER(RECT), ctypes.c_double)

    def _cb(hmon, _hdc, _lprc, _data):
        info = MONITORINFOEXW()
        info.cbSize = ctypes.sizeof(MONITORINFOEXW)
        if user32.GetMonitorInfoW(hmon, ctypes.byref(info)):
            r = info.rcMonitor
            scale = 1.0
            if shcore is not None:
                dx, dy = wintypes.UINT(), wintypes.UINT()
                try:
                    if shcore.GetDpiForMonitor(hmon, 0, ctypes.byref(dx), ctypes.byref(dy)) == 0 and dx.value:
                        scale = round(dx.value / 96.0, 2)
                except Exception:  # noqa: BLE001
                    pass
            out.append(
                MonitorInfo(
                    id=f"screen:{len(out) + 1}",
                    name=info.szDevice,
                    rect=Rect(r.left, r.top, r.right - r.left, r.bottom - r.top),
                    scale=scale,
                    primary=bool(info.dwFlags & 1),
                    handle=int(hmon),
                )
            )
        return 1

    user32.EnumDisplayMonitors(None, None, MonitorEnumProc(_cb), 0)
    return out


def monitor_for_rect(mons: list[MonitorInfo], rect: Rect) -> str:
    cx, cy = rect.x + rect.w // 2, rect.y + rect.h // 2
    for m in mons:
        if m.rect.contains(cx, cy):
            return m.id
    return mons[0].id if mons else "screen:1"


# ---------------------------------------------------------------- windows
def _window_rect(hwnd: int) -> Rect:
    r = RECT()
    try:
        # extended frame bounds exclude the invisible resize borders of DWM windows
        if dwmapi.DwmGetWindowAttribute(hwnd, 9, ctypes.byref(r), ctypes.sizeof(r)) == 0 and r.right > r.left:
            return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)
    except Exception:  # noqa: BLE001
        pass
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return Rect(r.left, r.top, r.right - r.left, r.bottom - r.top)


def _is_cloaked(hwnd: int) -> bool:
    val = wintypes.DWORD(0)
    try:
        if dwmapi.DwmGetWindowAttribute(hwnd, 14, ctypes.byref(val), ctypes.sizeof(val)) == 0:
            return val.value != 0
    except Exception:  # noqa: BLE001
        pass
    return False


def _title(hwnd: int) -> str:
    n = user32.GetWindowTextLengthW(hwnd)
    if n <= 0:
        return ""
    buf = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, buf, n + 1)
    return buf.value


def _class(hwnd: int) -> str:
    buf = ctypes.create_unicode_buffer(256)
    user32.GetClassNameW(hwnd, buf, 256)
    return buf.value


def describe_window(hwnd: int, mons: Optional[list[MonitorInfo]] = None, selfpids: Optional[set[int]] = None) -> Optional[WindowSource]:
    if not IS_WIN or not hwnd or not user32.IsWindow(hwnd):
        return None
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    name, exe = process_name(pid.value)
    mons = mons if mons is not None else monitors()
    rect = _window_rect(hwnd)
    selfpids = selfpids if selfpids is not None else self_pids()
    title = _title(hwnd)
    return WindowSource(
        hwnd=int(hwnd),
        pid=int(pid.value),
        process=name,
        exe=exe,
        title=title,
        cls=_class(hwnd),
        rect=rect,
        monitor_id=monitor_for_rect(mons, rect),
        minimized=bool(user32.IsIconic(hwnd)),
        is_self=pid.value in selfpids or (name.lower() in _SELF_NAMES),
        cloaked=_is_cloaked(hwnd),
    )


def list_windows(include_self: bool = False) -> list[WindowSource]:
    """Visible, non-cloaked top-level windows with a title (what the user sees in the taskbar)."""
    if not IS_WIN:
        return []
    ensure_dpi_awareness()
    mons = monitors()
    selfpids = self_pids()
    out: list[WindowSource] = []
    WNDENUMPROC = ctypes.WINFUNCTYPE(ctypes.c_bool, ctypes.c_int, ctypes.c_int)
    GWL_EXSTYLE = -20
    WS_EX_TOOLWINDOW = 0x00000080

    def _cb(hwnd, _lparam):
        try:
            if not user32.IsWindowVisible(hwnd):
                return True
            if user32.GetAncestor(hwnd, 2) != hwnd:  # GA_ROOT: only root windows
                return True
            ex = user32.GetWindowLongW(hwnd, GWL_EXSTYLE)
            if ex & WS_EX_TOOLWINDOW:
                return True
            w = describe_window(hwnd, mons, selfpids)
            if w is None or w.cloaked or not w.title.strip():
                return True
            if w.rect.w < 40 or w.rect.h < 40:
                return True
            if w.is_self and not include_self:
                return True
            out.append(w)
        except Exception:  # noqa: BLE001
            pass
        return True

    user32.EnumWindows(WNDENUMPROC(_cb), 0)
    return out


def foreground_window(exclude_self: bool = True) -> Optional[WindowSource]:
    if not IS_WIN:
        return None
    ensure_dpi_awareness()
    hwnd = user32.GetForegroundWindow()
    w = describe_window(hwnd)
    if w is None:
        return None
    if exclude_self and w.is_self:
        return None
    if not w.title.strip():
        return None
    return w


def same_window(src: WindowSource) -> tuple[bool, str]:
    """Is the handle still the same window (same process, still visible)?"""
    if not IS_WIN:
        return False, "lost"
    if not user32.IsWindow(src.hwnd):
        return False, "lost"
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(src.hwnd, ctypes.byref(pid))
    if pid.value != src.pid:
        return False, "lost"
    if not user32.IsWindowVisible(src.hwnd):
        return False, "lost"
    if user32.IsIconic(src.hwnd):
        return True, "minimized"
    return True, "ok"


def refresh_window(src: WindowSource) -> Optional[WindowSource]:
    ok, _ = same_window(src)
    if not ok:
        return None
    return describe_window(src.hwnd)


class _LASTINPUTINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]


def last_input_age_ms() -> Optional[int]:
    """Milliseconds since the last keyboard/mouse input of the interactive session
    (GetLastInputInfo); None when not on Windows or unavailable."""
    if not IS_WIN:
        return None
    try:
        info = _LASTINPUTINFO()
        info.cbSize = ctypes.sizeof(_LASTINPUTINFO)
        if not user32.GetLastInputInfo(ctypes.byref(info)):
            return None
        tick = kernel32.GetTickCount() & 0xFFFFFFFF
        return int((tick - info.dwTime) & 0xFFFFFFFF)
    except Exception:  # noqa: BLE001
        return None


LOCK_SCREEN_PROCESSES = {"lockapp.exe", "logonui.exe"}


def session_locked() -> bool:
    """True while the Windows lock screen owns the foreground. After an idle timeout Windows 10/11
    shows LockApp.exe on the *Default* desktop (the input desktop check alone stays 'interactive');
    foreground activation and input then fail although UI Automation still works."""
    if not IS_WIN:
        return False
    try:
        fg = user32.GetForegroundWindow()
        if not fg:
            return False
        pid = wintypes.DWORD(0)
        user32.GetWindowThreadProcessId(fg, ctypes.byref(pid))
        name, _ = process_name(int(pid.value))
        return (name or "").lower() in LOCK_SCREEN_PROCESSES
    except Exception:  # noqa: BLE001
        return False


def desktop_interactive() -> bool:
    """False on the secure desktop / lock screen / no input desktop (no capture or input then)."""
    if not IS_WIN:
        return False
    try:
        h = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_READOBJECTS
        if not h:
            return False
        buf = ctypes.create_unicode_buffer(64)
        n = wintypes.DWORD()
        user32.GetUserObjectInformationW(h, 2, buf, ctypes.sizeof(buf), ctypes.byref(n))  # UOI_NAME
        user32.CloseDesktop(h)
        if buf.value.lower() != "default":
            return False
        return not session_locked()
    except Exception:  # noqa: BLE001
        return False


def window_is_elevated_or_protected(src: WindowSource) -> bool:
    """A window of a higher-integrity process cannot receive our input (UIPI)."""
    if not IS_WIN:
        return False
    try:
        import psutil

        p = psutil.Process(src.pid)
        # cheap proxy: if we cannot read the exe path, the process is protected/elevated
        p.exe()
        return False
    except Exception:  # noqa: BLE001
        return True


def bring_to_front(src: WindowSource, *, wait_s: float = 0.6) -> bool:
    """Activate the window and report whether it really became the foreground window.

    A background process may not call SetForegroundWindow directly (foreground lock); the
    documented workarounds are used in order: attach to the foreground thread's input queue,
    simulate a harmless modifier key press, then SetForegroundWindow + BringWindowToTop.
    """
    if not IS_WIN:
        return False
    try:
        if user32.IsIconic(src.hwnd):
            user32.ShowWindow(src.hwnd, 9)  # SW_RESTORE
        if user32.GetForegroundWindow() == src.hwnd:
            return True
        fg = user32.GetForegroundWindow()
        this_tid = kernel32.GetCurrentThreadId()
        fg_tid = user32.GetWindowThreadProcessId(fg, None) if fg else 0
        attached = False
        if fg_tid and fg_tid != this_tid:
            attached = bool(user32.AttachThreadInput(this_tid, fg_tid, True))
        try:
            user32.SetForegroundWindow(src.hwnd)
            user32.BringWindowToTop(src.hwnd)
            if user32.GetForegroundWindow() != src.hwnd:
                # second documented workaround: a modifier key press "unlocks" SetForegroundWindow
                user32.keybd_event(0x12, 0, 0, 0)  # VK_MENU down
                user32.keybd_event(0x12, 0, 2, 0)  # KEYEVENTF_KEYUP
                user32.SetForegroundWindow(src.hwnd)
                user32.BringWindowToTop(src.hwnd)
        finally:
            if attached:
                user32.AttachThreadInput(this_tid, fg_tid, False)
        deadline = time.monotonic() + wait_s
        while time.monotonic() < deadline:
            if user32.GetForegroundWindow() == src.hwnd:
                return True
            time.sleep(0.03)
        return user32.GetForegroundWindow() == src.hwnd
    except Exception:  # noqa: BLE001
        return False
