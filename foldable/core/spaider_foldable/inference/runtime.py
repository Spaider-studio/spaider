"""llama-server process lifecycle.

One :class:`LlamaServerProcess` per loaded model (chat, embedding). The server
binds to 127.0.0.1 on a free port, is protected with a random API key, logs to
``data/logs/llama-<role>.log``, and is attached to a Windows Job Object so it
dies with the core process even on a crash (no lingering model servers).
"""

from __future__ import annotations

import ctypes
import logging
import os
import secrets
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class RuntimeError_(RuntimeError):
    pass


def free_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as s:
        s.bind(("127.0.0.1", 0))
        return int(s.getsockname()[1])


def runtime_executable(runtime_dir: Path) -> Path:
    exe = runtime_dir / ("llama-server.exe" if sys.platform == "win32" else "llama-server")
    if not exe.exists():
        raise RuntimeError_(f"llama-server nicht gefunden unter {exe}")
    return exe


def runtime_version(runtime_dir: Path) -> str:
    try:
        out = subprocess.run(
            [str(runtime_executable(runtime_dir)), "--version"],
            capture_output=True,
            text=True,
            timeout=20,
            creationflags=_no_window_flags(),
        )
        text = (out.stdout or "") + (out.stderr or "")
        return text.strip().splitlines()[0] if text.strip() else "unknown"
    except Exception as exc:  # noqa: BLE001
        return f"unknown ({exc})"


def _no_window_flags() -> int:
    if sys.platform == "win32":
        return subprocess.CREATE_NO_WINDOW | subprocess.CREATE_NEW_PROCESS_GROUP  # type: ignore[attr-defined]
    return 0


def _short_path_if_needed(path: Path) -> str:
    """llama.cpp opens the model path through the C runtime. Its Windows build
    converts UTF-8 paths, but as a safety net non-ASCII paths are passed as
    8.3 short paths when the volume provides them."""
    s = str(path)
    if sys.platform != "win32" or s.isascii():
        return s
    try:
        buf = ctypes.create_unicode_buffer(32768)
        n = ctypes.windll.kernel32.GetShortPathNameW(s, buf, len(buf))  # type: ignore[attr-defined]
        if n and buf.value and buf.value.isascii():
            return buf.value
    except Exception:  # noqa: BLE001
        pass
    return s


class _JobObject:
    """Kill-on-close job object: child processes end with the core."""

    _handle = None

    @classmethod
    def handle(cls):
        if sys.platform != "win32":
            return None
        if cls._handle is None:
            kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
            h = kernel32.CreateJobObjectW(None, None)
            if not h:
                return None

            class JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("PerProcessUserTimeLimit", ctypes.c_int64),
                    ("PerJobUserTimeLimit", ctypes.c_int64),
                    ("LimitFlags", ctypes.c_uint32),
                    ("MinimumWorkingSetSize", ctypes.c_size_t),
                    ("MaximumWorkingSetSize", ctypes.c_size_t),
                    ("ActiveProcessLimit", ctypes.c_uint32),
                    ("Affinity", ctypes.c_size_t),
                    ("PriorityClass", ctypes.c_uint32),
                    ("SchedulingClass", ctypes.c_uint32),
                ]

            class IO_COUNTERS(ctypes.Structure):
                _fields_ = [
                    (n, ctypes.c_uint64)
                    for n in (
                        "ReadOperationCount",
                        "WriteOperationCount",
                        "OtherOperationCount",
                        "ReadTransferCount",
                        "WriteTransferCount",
                        "OtherTransferCount",
                    )
                ]

            class JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
                _fields_ = [
                    ("BasicLimitInformation", JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ("IoInfo", IO_COUNTERS),
                    ("ProcessMemoryLimit", ctypes.c_size_t),
                    ("JobMemoryLimit", ctypes.c_size_t),
                    ("PeakProcessMemoryUsed", ctypes.c_size_t),
                    ("PeakJobMemoryUsed", ctypes.c_size_t),
                ]

            JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x2000
            JobObjectExtendedLimitInformation = 9
            info = JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            kernel32.SetInformationJobObject(
                h, JobObjectExtendedLimitInformation, ctypes.byref(info), ctypes.sizeof(info)
            )
            cls._handle = h
        return cls._handle

    @classmethod
    def assign(cls, pid: int) -> None:
        h = cls.handle()
        if h is None:
            return
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        PROCESS_SET_QUOTA = 0x0100
        PROCESS_TERMINATE = 0x0001
        ph = kernel32.OpenProcess(PROCESS_SET_QUOTA | PROCESS_TERMINATE, False, pid)
        if ph:
            try:
                kernel32.AssignProcessToJobObject(h, ph)
            finally:
                kernel32.CloseHandle(ph)


class LlamaServerProcess:
    def __init__(
        self,
        *,
        runtime_dir: Path,
        model_path: Path,
        role: str,
        log_path: Path,
        context_tokens: int = 8192,
        threads: Optional[int] = None,
        extra_args: Optional[list[str]] = None,
    ):
        self.runtime_dir = runtime_dir
        self.model_path = model_path
        self.role = role
        self.log_path = log_path
        self.context_tokens = context_tokens
        self.threads = threads
        self.extra_args = extra_args or []
        self.port: Optional[int] = None
        self.api_key = secrets.token_hex(16)
        self._proc: Optional[subprocess.Popen] = None
        self._log_fh = None

    @property
    def base_url(self) -> str:
        return f"http://127.0.0.1:{self.port}"

    @property
    def pid(self) -> Optional[int]:
        return self._proc.pid if self._proc else None

    def is_running(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    def command(self) -> list[str]:
        exe = runtime_executable(self.runtime_dir)
        threads = self.threads or max(1, (os.cpu_count() or 2))
        args = [
            str(exe),
            "-m",
            _short_path_if_needed(self.model_path),
            "--host",
            "127.0.0.1",
            "--port",
            str(self.port),
            "--api-key",
            self.api_key,
            "-t",
            str(threads),
            "--no-webui",
            "--log-timestamps",
        ]
        if self.role == "embedding":
            args += [
                "--embeddings",
                "-c",
                str(min(self.context_tokens, 8192)),
                "-b",
                "8192",
                "-ub",
                "8192",
                "--parallel",
                "1",
            ]
        else:
            args += ["-c", str(self.context_tokens), "--parallel", "1", "--jinja", "--reasoning-format", "deepseek"]
        args += self.extra_args
        return args

    def start(self) -> None:
        if self.is_running():
            return
        self.port = free_port()
        self.log_path.parent.mkdir(parents=True, exist_ok=True)
        self._log_fh = open(self.log_path, "ab")
        cmd = self.command()
        logger.info("starting llama-server (%s): %s", self.role, " ".join(cmd[:4]) + " ...")
        self._proc = subprocess.Popen(
            cmd,
            stdout=self._log_fh,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            cwd=str(self.runtime_dir),
            creationflags=_no_window_flags(),
        )
        _JobObject.assign(self._proc.pid)

    def wait_ready(self, timeout: float) -> None:
        """Poll /health until the model is loaded."""
        deadline = time.monotonic() + timeout
        last_err = ""
        with httpx.Client(timeout=5.0) as client:
            while time.monotonic() < deadline:
                if not self.is_running():
                    raise RuntimeError_(
                        f"llama-server ({self.role}) hat sich beendet (Exit-Code {self._proc.returncode})."
                        + self._log_tail()
                    )
                try:
                    r = client.get(f"{self.base_url}/health", headers={"Authorization": f"Bearer {self.api_key}"})
                    if r.status_code == 200:
                        return
                    last_err = f"HTTP {r.status_code}"
                except httpx.HTTPError as exc:
                    last_err = str(exc)
                time.sleep(0.4)
        raise RuntimeError_(
            f"llama-server ({self.role}) wurde nicht rechtzeitig bereit ({last_err})." + self._log_tail()
        )

    def stop(self, timeout: float = 5.0) -> None:
        """Stop the server. llama-server keeps no state that needs flushing (the
        model is memory-mapped read-only, logs are written by us), so a hard
        TerminateProcess after a short graceful attempt is safe and keeps
        shutdown under a second per model."""
        proc = self._proc
        if proc is None:
            return
        if proc.poll() is None:
            try:
                if sys.platform == "win32":
                    # Without a console (CREATE_NO_WINDOW) the break event may not arrive;
                    # give it a brief chance, then terminate.
                    proc.send_signal(subprocess.signal.CTRL_BREAK_EVENT)  # type: ignore[attr-defined]
                    proc.wait(timeout=0.5)
                else:
                    proc.terminate()
                    proc.wait(timeout=timeout)
            except Exception:  # noqa: BLE001
                pass
            if proc.poll() is None:
                try:
                    proc.terminate()
                    proc.wait(timeout=timeout)
                except Exception:  # noqa: BLE001
                    pass
            if proc.poll() is None:
                proc.kill()
                try:
                    proc.wait(timeout=5)
                except Exception:  # noqa: BLE001
                    pass
        self._proc = None
        if self._log_fh:
            try:
                self._log_fh.close()
            except OSError:
                pass
            self._log_fh = None
        logger.info("llama-server (%s) stopped", self.role)

    def _log_tail(self, lines: int = 12) -> str:
        try:
            text = self.log_path.read_text(encoding="utf-8", errors="replace").splitlines()[-lines:]
            return "\n" + "\n".join(text) if text else ""
        except OSError:
            return ""
