"""Fresh-start check of the assembled portable package (no developer tools on
PATH, no models): unzip into a directory with spaces and umlauts, verify the
layout, start the packaged core from inside the package, check the API, shut
it down, and (optionally) launch SpAIder.exe once so it saves a screenshot of
the setup screen.

Usage:
  python foldable/build/verify_package.py foldable/build/out/SpAIder-Foldable-0.1.0-win-x64.zip
      [--screenshot <png>] [--keep]
"""

from __future__ import annotations

import argparse
import json
import os
import secrets
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.request
import zipfile
from pathlib import Path

EXPECTED = [
    "SpAIder/SpAIder.exe",
    "SpAIder/resources/app.asar",
    "SpAIder/resources/core/spaider-foldable-core.exe",
    "SpAIder/resources/runtime/llama/llama-server.exe",
    "SpAIder/resources/runtime/llama/ggml.dll",
    "SpAIder/licenses/LICENSE-SpAIder-AGPL-3.0.txt",
    "SpAIder/licenses/LICENSE-llama.cpp",
    "SpAIder/README-ERSTE-SCHRITTE.txt",
    "SpAIder/BUILD_INFO.txt",
]


def _api(port: int, token: str, path: str, method: str = "GET") -> dict:
    req = urllib.request.Request(f"http://127.0.0.1:{port}/api{path}", method=method, headers={"X-SpAIder-Token": token})
    with urllib.request.urlopen(req, timeout=30) as resp:  # noqa: S310 (loopback)
        return json.loads(resp.read().decode("utf-8"))


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("zip", type=Path)
    ap.add_argument("--screenshot", type=Path, default=None)
    ap.add_argument("--keep", action="store_true")
    args = ap.parse_args()
    if not args.zip.exists():
        print(f"ERROR: {args.zip} missing", file=sys.stderr)
        return 1
    base = Path(tempfile.mkdtemp(prefix="SpAIder Prüfung ü "))
    print(f"extracting to {base}")
    with zipfile.ZipFile(args.zip) as zf:
        zf.extractall(base)
    root = next(base.iterdir())  # SpAIder-Foldable-<ver>-win-x64/
    missing = [e for e in EXPECTED if not (root / e).exists()]
    if missing:
        print("ERROR: missing files:", missing, file=sys.stderr)
        return 1
    print("layout ok:", len(EXPECTED), "expected files present")
    app = root / "SpAIder"
    assert not (app / "data").exists(), "package must not contain a data directory"

    # Start the packaged core exactly as the launcher does, with a bare PATH.
    token = secrets.token_hex(16)
    env = {
        "PATH": r"C:\Windows\System32;C:\Windows",
        "SYSTEMROOT": os.environ.get("SYSTEMROOT", r"C:\Windows"),
        "TEMP": os.environ.get("TEMP", ""),
        "TMP": os.environ.get("TMP", ""),
        "SPAIDER_FOLDABLE_TOKEN": token,
        "SPAIDER_FOLDABLE_RUNTIME_DIR": str(app / "resources" / "runtime" / "llama"),
    }
    t0 = time.monotonic()
    proc = subprocess.Popen(
        [str(app / "resources" / "core" / "spaider-foldable-core.exe"), "--app-root", str(app)],
        env=env, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8", errors="replace",
    )
    assert proc.stdout is not None
    port = None
    while True:
        line = proc.stdout.readline()
        if not line:
            print("ERROR: core exited:", proc.poll(), (proc.stderr.read() if proc.stderr else "")[-800:], file=sys.stderr)
            return 1
        try:
            evt = json.loads(line)
        except ValueError:
            continue
        if evt.get("event") == "ready":
            port = int(evt["port"])
            break
        if evt.get("event") == "error":
            print("ERROR:", evt, file=sys.stderr)
            return 1
    start_s = round(time.monotonic() - t0, 2)
    print(f"core ready in {start_s}s on port {port}")
    health = _api(port, token, "/health")
    setup = _api(port, token, "/setup/status")
    info = _api(port, token, "/system/info")
    print("health:", health["status"], "| setup:", setup["state"], "| runtime:", info["runtime"]["version"])
    assert setup["state"] == "needs_model"
    assert Path(info["data_dir"]).resolve() == (app / "data").resolve(), info["data_dir"]
    try:
        urllib.request.urlopen(urllib.request.Request(f"http://127.0.0.1:{port}/api/health"), timeout=5)  # noqa: S310
        print("ERROR: request without token was accepted", file=sys.stderr)
        return 1
    except urllib.error.HTTPError as exc:
        assert exc.code == 401
        print("token required: ok")
    _api(port, token, "/system/shutdown", method="POST")
    proc.wait(timeout=60)
    print(f"core exit code {proc.returncode}; lock removed: {not (app / 'data' / 'instance.lock').exists()}")
    result = {"zip": str(args.zip), "extracted_to": str(root), "core_start_s": start_s, "runtime": info["runtime"], "setup_state": setup["state"]}

    if args.screenshot:
        env2 = dict(os.environ)
        # Some terminals (VS Code, agent harnesses) export ELECTRON_RUN_AS_NODE=1,
        # which turns any Electron binary into a plain Node process that exits at once.
        env2.pop("ELECTRON_RUN_AS_NODE", None)
        env2["SPAIDER_FOLDABLE_SCREENSHOT"] = str(args.screenshot.resolve())
        t1 = time.monotonic()
        launcher = subprocess.run([str(app / "SpAIder.exe")], env=env2, timeout=180, capture_output=True)
        result["launcher_exit"] = launcher.returncode
        result["launcher_wall_s"] = round(time.monotonic() - t1, 1)
        result["screenshot"] = str(args.screenshot.resolve()) if args.screenshot.exists() else None
        print("launcher exit", launcher.returncode, "screenshot:", result["screenshot"])
    print(json.dumps(result, indent=2, ensure_ascii=False))
    if not args.keep:
        shutil.rmtree(base, ignore_errors=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
