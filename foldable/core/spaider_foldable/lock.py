"""Single-instance lock for one data directory.

A JSON lock file records pid, hostname and start time. A second start on the
same host while the first process is alive is refused with a clear German
message. A stale lock (crash, or the drive was moved to another host where the
pid cannot be checked) is taken over and logged.
"""

from __future__ import annotations

import json
import logging
import os
import socket
import sys
import time
from pathlib import Path

logger = logging.getLogger(__name__)


class AlreadyRunningError(RuntimeError):
    def __init__(self, info: dict):
        self.info = info
        super().__init__(
            "SpAIder läuft bereits mit diesem Datenverzeichnis "
            f"(PID {info.get('pid')}, gestartet {info.get('started_at')}). "
            "Bitte zuerst die laufende Instanz beenden."
        )


def _pid_alive(pid: int) -> bool:
    if pid <= 0:
        return False
    if sys.platform == "win32":
        import ctypes

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        STILL_ACTIVE = 259
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        handle = kernel32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            if not kernel32.GetExitCodeProcess(handle, ctypes.byref(code)):
                return False
            return code.value == STILL_ACTIVE
        finally:
            kernel32.CloseHandle(handle)
    try:
        os.kill(pid, 0)
        return True
    except OSError:
        return False


class InstanceLock:
    def __init__(self, path: Path):
        self.path = path
        self._held = False

    def _read(self) -> dict | None:
        try:
            return json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def acquire(self) -> None:
        if self._held:
            return  # already ours (the lock object is shared across profile contexts)
        info = {
            "pid": os.getpid(),
            "hostname": socket.gethostname(),
            "started_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime()) + "Z",
        }
        existing = self._read() if self.path.exists() else None
        if existing is not None:
            same_host = existing.get("hostname") == info["hostname"]
            if same_host and _pid_alive(int(existing.get("pid") or 0)):
                raise AlreadyRunningError(existing)
            logger.warning(
                "Stale instance lock found (pid=%s host=%s, started %s); taking over.",
                existing.get("pid"),
                existing.get("hostname"),
                existing.get("started_at"),
            )
            try:
                self.path.unlink()
            except OSError:
                pass
        # Exclusive create closes the race between two simultaneous starts.
        fd = os.open(self.path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(info, fh)
        self._held = True

    def release(self) -> None:
        if not self._held:
            return
        try:
            self.path.unlink()
        except OSError:
            pass
        self._held = False

    @property
    def held(self) -> bool:
        return self._held
