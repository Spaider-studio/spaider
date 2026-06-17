"""Docker detection + stack lifecycle for ``spaider init``.

Surface intentionally narrow — we only need:

- ``check_docker_available()``: is the ``docker`` CLI installed AND the daemon running?
- ``compose_up(repo_root)``: bring up ``docker compose -f ... -f docker-compose.dev.yml up -d backend-api``.
- ``wait_for_backend(timeout)``: poll ``http://localhost:8000/health`` until it returns ``healthy: true``.

All subprocess calls go through ``subprocess.run`` with explicit ``capture_output``
so we don't leak Docker progress noise into the rich-printed wizard UI.
"""
from __future__ import annotations

import shutil
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

import httpx

# ---------------------------------------------------------------------------
# Detection
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class DockerStatus:
    cli_present: bool
    daemon_running: bool
    error: str | None = None


def check_docker_available() -> DockerStatus:
    """Inspect Docker's state without raising.

    Returns ``cli_present`` and ``daemon_running``. Caller decides what to do
    on red — e.g. ``spaider init`` blocks with an actionable message; ``spaider doctor``
    just reports.
    """
    if shutil.which("docker") is None:
        return DockerStatus(cli_present=False, daemon_running=False)
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError) as exc:
        return DockerStatus(cli_present=True, daemon_running=False, error=str(exc))
    if result.returncode != 0:
        return DockerStatus(
            cli_present=True,
            daemon_running=False,
            error=(result.stderr or result.stdout or "").strip()[:200],
        )
    return DockerStatus(cli_present=True, daemon_running=True)


# ---------------------------------------------------------------------------
# Compose lifecycle
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class ComposeResult:
    ok: bool
    stdout: str
    stderr: str
    returncode: int


def compose_up(repo_root: Path, service: str = "backend-api") -> ComposeResult:
    """Bring up the dev stack via ``docker compose -f ... up -d <service>``."""
    cmd = [
        "docker", "compose",
        "-f", str(repo_root / "docker-compose.yml"),
        "-f", str(repo_root / "docker-compose.dev.yml"),
        "up", "-d", service,
    ]
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=300)
    return ComposeResult(
        ok=result.returncode == 0,
        stdout=result.stdout,
        stderr=result.stderr,
        returncode=result.returncode,
    )


# ---------------------------------------------------------------------------
# Healthcheck
# ---------------------------------------------------------------------------


def wait_for_backend(
    *,
    base_url: str = "http://localhost:8000",
    timeout_s: float = 120.0,
    poll_interval_s: float = 2.0,
) -> bool:
    """Poll the ``/health`` endpoint until it reports healthy or we time out.

    Returns True if the backend went healthy within the budget. Does not raise
    on per-poll errors — the wait loop swallows transient connect-refused.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            with httpx.Client(timeout=2.0) as c:
                resp = c.get(f"{base_url}/health")
                if resp.status_code == 200 and resp.json().get("healthy") is True:
                    return True
        except Exception:
            pass
        time.sleep(poll_interval_s)
    return False
