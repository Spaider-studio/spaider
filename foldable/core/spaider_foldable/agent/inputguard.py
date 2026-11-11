"""InputGuard: one input lock per desktop, user-activity detection and the
release of held keys on stop.

* Only one agent action may inject keyboard/mouse input at a time (no
  competing mouse agents; the lock also serialises window focus changes).
* If the user touched keyboard or mouse after our last injection (plus a
  short grace period), the next input action is refused with
  ``user_active`` and the task pauses instead of fighting the user.
* ``release_held_keys`` sends key-up for every modifier so a stop never
  leaves Ctrl/Alt/Shift/Win pressed; it is safe to call when nothing is held.
"""

from __future__ import annotations

import asyncio
import ctypes
import sys
import time
from typing import Optional

from ..observe import windows as W

GRACE_MS = 900  # user input younger than this (and not ours) pauses the control
MODIFIERS = (0x10, 0x11, 0x12, 0x5B, 0x5C, 0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5)  # Shift, Ctrl, Alt, LWin, RWin, L/R variants


class UserActive(Exception):
    def __init__(self, idle_ms: int):
        super().__init__(f"user input {idle_ms} ms ago")
        self.idle_ms = idle_ms


class InputGuard:
    def __init__(self, *, enabled: bool = True):
        self.enabled = enabled
        self.lock = asyncio.Lock()
        self._own_until = 0.0  # monotonic: input until this time is ours
        self._own_started = 0.0
        self.paused_by_user = 0
        self.released = 0

    # ---- own input bookkeeping -----------------------------------------------------------
    def mark_own_input(self, duration_s: float = 1.0) -> None:
        now = time.monotonic()
        self._own_started = now
        self._own_until = max(self._own_until, now + max(0.05, duration_s))

    def extend_own_input(self, duration_s: float = 0.5) -> None:
        self._own_until = max(self._own_until, time.monotonic() + duration_s)

    # ---- checks ---------------------------------------------------------------------------
    def user_input_age_ms(self) -> Optional[int]:
        return W.last_input_age_ms()

    def check_user_idle(self, *, grace_ms: int = GRACE_MS) -> None:
        """Raise ``UserActive`` when the user produced input recently that was not ours."""
        if not self.enabled:
            return
        age = self.user_input_age_ms()
        if age is None:
            return
        now = time.monotonic()
        if now < self._own_until:
            # inside our own injection window: input younger than the window start is ours
            own_age_ms = int((now - self._own_started) * 1000)
            if age <= own_age_ms + 50:
                return
        if age < grace_ms:
            self.paused_by_user += 1
            raise UserActive(age)

    def unexpected_foreground(self, expected_hwnd: int) -> Optional[str]:
        """Title of the foreground window when it is neither the target nor one of ours."""
        fg = W.foreground_window(exclude_self=True)
        if fg is None:
            return None
        if fg.hwnd == expected_hwnd:
            return None
        return fg.title or fg.process

    # ---- stop ------------------------------------------------------------------------------
    def release_held_keys(self) -> int:
        if sys.platform != "win32":
            return 0
        n = 0
        try:
            user32 = ctypes.windll.user32
            for vk in MODIFIERS:
                if user32.GetAsyncKeyState(vk) & 0x8000:
                    user32.keybd_event(vk, 0, 2, 0)  # KEYEVENTF_KEYUP
                    n += 1
            # a held left mouse button (drag) is released too
            if user32.GetAsyncKeyState(0x01) & 0x8000:
                user32.mouse_event(0x0004, 0, 0, 0, 0)  # MOUSEEVENTF_LEFTUP
                n += 1
        except Exception:  # noqa: BLE001
            return n
        self.released += n
        return n
