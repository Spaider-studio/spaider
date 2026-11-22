"""A disposable copy of the full stack for browser and chaos tests.

The rig is a Docker-in-Docker container, so its containers, volumes and ports
never touch the host's own Docker. Only three ports are published, all on the
host's loopback:

    13000  Studio        18000  API        18080  Kong gateway

It runs against a deterministic LLM stub, with generated secrets. The browser must
not reach the real stack on 8000, so the Studio image is built with
NEXT_PUBLIC_BACKEND_URL pointing at the rig's API (see docker-compose.rig.yml) and
the API allows the rig's Studio origin through CORS_ORIGINS.

Usage (from the repository root):

    python backend/tests/rig/rig.py create      # start the container
    python backend/tests/rig/rig.py sync        # copy the working tree in
    python backend/tests/rig/rig.py up          # build and start the stack
    python backend/tests/rig/rig.py reset       # empty databases, same images
    python backend/tests/rig/rig.py env KEY=VALUE ...   # change .env and recreate what depends on it
    python backend/tests/rig/rig.py rebuild [service ...]   # sync, rebuild and restart services
    python backend/tests/rig/rig.py exec -- docker ps
    python backend/tests/rig/rig.py down        # remove everything
"""
from __future__ import annotations

import argparse
import base64
import io
import json
import os
import re
import secrets
import subprocess
import sys
import tarfile
import time
import urllib.request
from pathlib import Path

NAME = "spaider-rig"
ROOT = Path(__file__).resolve().parents[3]
STATE = Path(__file__).resolve().parent / ".rig-state.json"
KEYS = Path(__file__).resolve().parent / ".rig-keys.json"
REMOTE = "/root/s"
STUDIO = "http://localhost:13000"
API = "http://localhost:18000"
COMPOSE = "docker compose -f docker-compose.yml -f backend/tests/rig/docker-compose.rig.yml"

ENV_OVERRIDES = {
    "COMPOSE_PROFILES": "full",
    "SPAIDER_BIND": "0.0.0.0",
    "ENVIRONMENT": "development",
    # The Studio is served on 13000 here, not 3000.
    "CORS_ORIGINS": "http://localhost:13000",
    "LLM_PROVIDER": "openai",
    "LLM_MODEL": "gpt-4o-mini",
    "LLM_API_KEY": "stub-key",
    "LLM_BASE_URL": "http://llm-stub:9999/v1",
    "EMBEDDING_PROVIDER": "openai",
    "EMBEDDING_MODEL": "text-embedding-3-small",
    "EMBEDDING_API_KEY": "stub-key",
    "EMBEDDING_BASE_URL": "http://llm-stub:9999/v1",
    "EMBEDDING_DIMENSIONS": "256",
}


def run(*cmd: str, check: bool = True, capture: bool = False, stdin: bytes | None = None):
    return subprocess.run(cmd, check=check, text=not stdin, input=stdin, capture_output=capture)


def in_rig(command: str, check: bool = True, capture: bool = False):
    return run("docker", "exec", NAME, "sh", "-c", command, check=check, capture=capture)


def _tree_files() -> list[str]:
    listing = run("git", "-C", str(ROOT), "ls-files", "-z", "--cached", "--others", "--exclude-standard", capture=True)
    names = [n for n in listing.stdout.split("\0") if n]
    return sorted({n for n in names if (ROOT / n).is_file()})


def _tar_tree() -> bytes:
    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        for name in _tree_files():
            archive.add(ROOT / name, arcname=name, recursive=False)
    return buffer.getvalue()


# --- commands ----------------------------------------------------------------


def create() -> None:
    down()
    run(
        "docker", "run", "-d", "--privileged", "--name", NAME, "-e", "DOCKER_TLS_CERTDIR=",
        "-p", "127.0.0.1:13000:3000", "-p", "127.0.0.1:18000:8000", "-p", "127.0.0.1:18080:8080",
        "docker:dind", capture=True,
    )
    for _ in range(60):
        if run("docker", "exec", NAME, "docker", "info", check=False, capture=True).returncode == 0:
            break
        time.sleep(2)
    else:
        sys.exit("the rig's Docker daemon did not start")
    in_rig("apk add -q curl", capture=True)
    print("rig container ready")


def _env_text() -> str:
    text = (ROOT / ".env.example").read_text(encoding="utf-8")
    values = dict(ENV_OVERRIDES)
    values["NEO4J_PASSWORD"] = secrets.token_urlsafe(18)
    values["SPAIDER_BOOTSTRAP_TOKEN"] = secrets.token_hex(32)
    values["CONNECTOR_SECRET_KEY"] = base64.b64encode(secrets.token_bytes(32)).decode()
    for key, value in values.items():
        line = f"{key}={value}"
        text, count = re.subn(rf"^{key}=.*$", lambda _m, line=line: line, text, flags=re.M)
        if count == 0:
            text += f"\n{line}\n"
    fd = os.open(STATE, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w") as handle:
        json.dump({"bootstrap_token": values["SPAIDER_BOOTSTRAP_TOKEN"], "api": API, "studio": STUDIO}, handle)
    return text


def sync() -> None:
    in_rig(f"mkdir -p {REMOTE} && cd {REMOTE} && find . -mindepth 1 -maxdepth 1 ! -name .env -exec rm -rf {{}} +")
    run("docker", "exec", "-i", NAME, "tar", "-x", "-C", REMOTE, stdin=_tar_tree())
    if in_rig(f"test -f {REMOTE}/.env", check=False).returncode != 0:
        run("docker", "exec", "-i", NAME, "sh", "-c", f"cat > {REMOTE}/.env", stdin=_env_text().encode())
    print("working tree synced")


def up() -> None:
    result = in_rig(f"cd {REMOTE} && {COMPOSE} up -d --build", check=False, capture=True)
    sys.stdout.write((result.stdout or "")[-1500:])
    sys.stderr.write((result.stderr or "")[-1500:])
    if result.returncode != 0:
        sys.exit("compose up failed")
    _wait_healthy()


def _wait_healthy() -> None:
    deadline = time.time() + 300
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(f"{API}/health", timeout=5) as response:
                health = json.load(response)
            if all(state == "ok" for state in health["services"].values()):
                print("stack healthy:", health["services"])
                return
        except Exception:  # noqa: BLE001
            pass
        time.sleep(5)
    sys.exit("the stack did not become healthy within 300 s")


def reset() -> None:
    """Throw away all data but keep the built images, which take minutes to make."""
    in_rig(f"cd {REMOTE} && {COMPOSE} down -v", capture=True)
    KEYS.unlink(missing_ok=True)
    result = in_rig(f"cd {REMOTE} && {COMPOSE} up -d", check=False, capture=True)
    if result.returncode != 0:
        sys.exit("compose up failed: " + (result.stderr or "")[-500:])
    _wait_healthy()


def set_env(pairs: list[str]) -> None:
    """Change values in the rig's .env and recreate the services that use them."""
    for pair in pairs:
        key, _, value = pair.partition("=")
        if not re.fullmatch(r"[A-Z0-9_]+", key) or "'" in value or "\n" in value:
            sys.exit(f"bad setting: {pair!r}")
        in_rig(
            f"cd {REMOTE} && if grep -q '^{key}=' .env; then sed -i 's|^{key}=.*|{key}={value}|' .env; "
            f"else echo '{key}={value}' >> .env; fi"
        )
    result = in_rig(f"cd {REMOTE} && {COMPOSE} up -d", check=False, capture=True)
    if result.returncode != 0:
        sys.exit("compose up failed: " + (result.stderr or "")[-500:])
    _wait_healthy()


def rebuild(services: list[str]) -> None:
    """Copy the working tree in again and rebuild only the named services (default: the backend)."""
    sync()
    wanted = services or ["backend-api", "backend-worker"]
    if "frontend" in wanted:
        # The Next.js build needs several GB, and the rig shares the Docker VM's
        # memory with everything else. Without this it fails with "cannot allocate
        # memory" and the old frontend keeps running. Volumes keep the data.
        in_rig(f"cd {REMOTE} && {COMPOSE} stop", check=False, capture=True)
    result = in_rig(f"cd {REMOTE} && {COMPOSE} up -d --build {' '.join(wanted)}", check=False, capture=True)
    if result.returncode != 0:
        sys.exit("compose up failed: " + (result.stdout or "")[-300:] + (result.stderr or "")[-700:])
    in_rig(f"cd {REMOTE} && {COMPOSE} up -d", check=False, capture=True)
    in_rig("docker restart spaider-kong >/dev/null 2>&1 || true", check=False)
    _wait_healthy()


def down() -> None:
    run("docker", "rm", "-fv", NAME, check=False, capture=True)
    STATE.unlink(missing_ok=True)
    KEYS.unlink(missing_ok=True)


def exec_(command: list[str]) -> None:
    sys.exit(subprocess.run(["docker", "exec", NAME, "sh", "-c", f"cd {REMOTE} && " + " ".join(command)]).returncode)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["create", "sync", "up", "reset", "env", "rebuild", "down", "exec"])
    parser.add_argument("rest", nargs="*")
    args = parser.parse_args()
    if args.command == "exec":
        exec_([part for part in args.rest if part != "--"])
    elif args.command == "env":
        set_env(args.rest)
    elif args.command == "rebuild":
        rebuild(args.rest)
    else:
        {"create": create, "sync": sync, "up": up, "reset": reset, "down": down}[args.command]()


if __name__ == "__main__":
    main()
