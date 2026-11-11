"""Offline run (opt-in, needs the real runtime + models like test_real_e2e).

Without administrator rights the network adapter cannot be disabled, so this
test simulates "no external network" in two independent ways and reports both:

1. Every HTTP(S) library that honours proxy variables (httpx, urllib, openai /
   litellm) is pointed at a dead proxy (``http://127.0.0.1:1``) with
   ``NO_PROXY=127.0.0.1,localhost``; any attempt to reach the internet fails
   immediately.
2. While the core, the chat model and a document import run, the TCP
   connections of the core process and every llama-server process are sampled
   with ``netstat -ano``; any connection whose remote address is not loopback
   is reported as a violation.

A real air-gap test (adapter off / cable out) is documented in TEST_REPORT.md
as a manual step.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path

import httpx

from spaider_foldable.db import Database
from spaider_foldable.models.catalog import load_catalog
from spaider_foldable.models.registry import ModelRegistry
from spaider_foldable.paths import PortablePaths

from .conftest import CHAT_MODEL, requires_real
from .test_real_e2e import TOKEN, Core, _chat, _wait_job, _wait_runtime_ready

REPORT_ENV = "SPAIDER_FOLDABLE_TEST_REPORT_OFFLINE"


def _connections_for(pids: set[int]) -> list[str]:
    out = subprocess.run(["netstat", "-ano", "-p", "TCP"], capture_output=True).stdout.decode("utf-8", errors="replace")
    bad = []
    for line in out.splitlines():
        m = re.match(r"\s*TCP\s+(\S+)\s+(\S+)\s+(\S+)\s+(\d+)\s*$", line)
        if not m:
            continue
        local, remote, state, pid = m.groups()
        if int(pid) not in pids:
            continue
        host = remote.rsplit(":", 1)[0]
        if host in ("127.0.0.1", "[::1]", "0.0.0.0", "[::]", "*") or state == "LISTENING":
            continue
        bad.append(f"{local} -> {remote} {state} pid={pid}")
    return bad


def _llama_pids() -> set[int]:
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"], capture_output=True
    ).stdout.decode("utf-8", errors="replace")  # noqa: E501
    return {int(m.group(1)) for m in re.finditer(r'"llama-server.exe","(\d+)"', out)}


@requires_real()
def test_offline_chat_and_import_without_external_connections(tmp_path):
    app_root = tmp_path / "Offline ü" / "SpAIder"
    app_root.mkdir(parents=True)
    paths = PortablePaths.from_app_root(app_root)
    paths.ensure()
    cat = load_catalog()
    db = Database(paths.db_path)
    db.open()
    reg = ModelRegistry(db, paths)
    entry = cat.get("qwen3-0.6b-q8_0")
    part = paths.downloads_dir / "prep" / entry.files[0].path
    part.parent.mkdir(parents=True)
    shutil.copy2(Path(CHAT_MODEL), part)
    model = reg.install_from_download(entry, part, sha_verified=True)
    reg.set_state("active_chat_model_id", model["id"])  # pre-selected like a prepared stick
    db.close()

    os.environ["HTTP_PROXY"] = "http://127.0.0.1:1"
    os.environ["HTTPS_PROXY"] = "http://127.0.0.1:1"
    os.environ["ALL_PROXY"] = "http://127.0.0.1:1"
    os.environ["NO_PROXY"] = "127.0.0.1,localhost"
    report: dict = {"violations": [], "samples": 0}
    core = Core(app_root, label="offline").start()
    try:
        pids = {core.proc.pid}
        with httpx.Client(verify=False, trust_env=False, base_url=core.base, headers={"X-SpAIder-Token": TOKEN}, timeout=120) as c:
            active = _wait_runtime_ready(c)  # autostart of the pre-selected model, no network involved
            assert active["chat"]["id"] == model["id"]
            pids |= _llama_pids()
            report["violations"] += _connections_for(pids)
            report["samples"] += 1
            conv = c.post("/conversations", json={"title": "Offline"}).json()
            r = _chat(c, conv["id"], "Antworte mit einem kurzen Satz: Was ist die Hauptstadt von Frankreich?")
            assert r["done"] and r["done"]["status"] == "complete" and r["text"].strip(), r
            report["answer"] = r["text"][:200]
            report["violations"] += _connections_for(pids)
            report["samples"] += 1
            doc_src = tmp_path / "offline notiz.md"
            doc_src.write_text("# Notiz\n\nDer Offline-Test lief am Dienstag in Köln.\n", encoding="utf-8")
            with open(doc_src, "rb") as fh:
                doc = c.post("/documents", files={"file": (doc_src.name, fh, "text/markdown")}).json()
            assert doc["status"] in ("indexing", "indexed")
            for _ in range(120):
                if c.get("/memory/overview").json()["pending_ingest"] == 0:
                    break
                report["violations"] += _connections_for(pids)
                report["samples"] += 1
                time.sleep(1)
            # a download attempt must fail fast instead of hanging, and must not break anything
            job = c.post("/models/download", json={"catalog_id": "qwen3-1.7b-q8_0"}).json()["job"]
            job = _wait_job(c, job["id"], timeout=120)
            assert job["state"] in ("paused", "failed"), job
            report["download_offline"] = {"state": job["state"], "error": job["error"]}
            assert c.post(f"/models/jobs/{job['id']}/cancel").status_code == 200
            report["violations"] += _connections_for(pids)
            report["samples"] += 1
            r2 = _chat(c, conv["id"], "Wo lief der Offline-Test?")
            assert "Köln" in r2["text"] or "Koeln" in r2["text"], r2["text"]
            report["answer_doc"] = r2["text"][:200]
        core.shutdown()
    except BaseException:
        core.kill()
        raise
    finally:
        for k in ("HTTP_PROXY", "HTTPS_PROXY", "ALL_PROXY", "NO_PROXY"):
            os.environ.pop(k, None)
        p = os.environ.get(REPORT_ENV)
        if p:
            Path(p).write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    assert not report["violations"], report["violations"]
