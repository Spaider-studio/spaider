"""End-to-end acceptance run against the REAL core process and the REAL
llama.cpp runtime with a small open model. Opt-in (slow, needs the models):

    SPAIDER_FOLDABLE_REAL_TESTS=1
    SPAIDER_FOLDABLE_RUNTIME_DIR=<dir with llama-server.exe>
    SPAIDER_FOLDABLE_TEST_CHAT_MODEL=<path to Qwen3-0.6B-Q8_0.gguf>
    SPAIDER_FOLDABLE_TEST_EMBED_MODEL=<path to Qwen3-Embedding-0.6B-Q8_0.gguf>   (optional)
    SPAIDER_FOLDABLE_TEST_REPORT=<json file for measurements>                      (optional)

Covers the acceptance cases: fresh start, restart persistence, cross-chat
memory with a correction, document import with the original removed,
model switch failure with rollback, stop, lifecycle, move to another path
(different drive letter via ``subst``, spaces, umlauts).
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path

import httpx

from spaider_foldable.db import Database
from spaider_foldable.models.catalog import load_catalog
from spaider_foldable.models.registry import ModelRegistry
from spaider_foldable.paths import PortablePaths

from .conftest import CHAT_MODEL, EMBED_MODEL, RUNTIME_DIR, requires_real
from .gguf_fixture import write_fake_gguf

TOKEN = "e2e-token-" + "x" * 24
REPORT: dict = {"cases": {}, "measurements": {}}
CORE_DIR = Path(__file__).resolve().parents[1]


def _report(path_env: str = "SPAIDER_FOLDABLE_TEST_REPORT") -> None:
    p = os.environ.get(path_env)
    if p:
        Path(p).write_text(json.dumps(REPORT, indent=2, ensure_ascii=False), encoding="utf-8")


class Core:
    def __init__(self, app_root: Path, *, label: str):
        self.app_root = app_root
        self.label = label
        self.proc: subprocess.Popen | None = None
        self.port: int | None = None
        self.started_in: float | None = None

    def start(self) -> "Core":
        env = dict(os.environ)
        env["SPAIDER_FOLDABLE_TOKEN"] = TOKEN
        env["SPAIDER_FOLDABLE_RUNTIME_DIR"] = RUNTIME_DIR or ""
        env["PYTHONIOENCODING"] = "utf-8"
        t0 = time.monotonic()
        self.proc = subprocess.Popen(
            [sys.executable, "-m", "spaider_foldable", "--app-root", str(self.app_root)],
            cwd=str(CORE_DIR),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            encoding="utf-8",
        )
        assert self.proc.stdout is not None
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError(f"core exited early (code {self.proc.poll()})")
            try:
                evt = json.loads(line)
            except ValueError:
                continue
            if evt.get("event") == "ready":
                self.port = int(evt["port"])
                break
            if evt.get("event") == "error":
                raise RuntimeError(f"core error: {evt}")
        self.started_in = round(time.monotonic() - t0, 2)
        return self

    @property
    def base(self) -> str:
        return f"http://127.0.0.1:{self.port}/api"

    def client(self, timeout: float = 120.0) -> httpx.Client:
        return httpx.Client(verify=False, trust_env=False, base_url=self.base, headers={"X-SpAIder-Token": TOKEN}, timeout=timeout)

    def shutdown(self, timeout: float = 60.0) -> float:
        t0 = time.monotonic()
        with self.client() as c:
            assert c.post("/system/shutdown").json()["ok"]
        assert self.proc is not None
        self.proc.wait(timeout=timeout)
        return round(time.monotonic() - t0, 2)

    def kill(self) -> None:
        if self.proc and self.proc.poll() is None:
            subprocess.run(["taskkill", "/PID", str(self.proc.pid), "/T", "/F"], capture_output=True)


def _sse_events(resp: httpx.Response):
    event, data = None, []
    for line in resp.iter_lines():
        if line.startswith("event:"):
            event = line[6:].strip()
        elif line.startswith("data:"):
            data.append(line[5:].strip())
        elif line == "":
            if event:
                yield event, json.loads("\n".join(data) or "{}")
            event, data = None, []


def _wait_job(c: httpx.Client, job_id: str, timeout: float = 600) -> dict:
    deadline = time.monotonic() + timeout
    with c.stream("GET", f"/models/jobs/{job_id}/events", timeout=timeout) as r:
        last = None
        for evt, job in _sse_events(r):
            last = job
            if job["state"] in ("done", "failed", "cancelled", "paused"):
                return job
            if time.monotonic() > deadline:
                break
    return last or c.get(f"/models/jobs/{job_id}").json()["job"]


def _wait_runtime_ready(c: httpx.Client, timeout: float = 180) -> dict:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        a = c.get("/models/active").json()
        if a["runtime_state"] == "ready":
            return a
        if a["runtime_state"] == "error":
            raise AssertionError(f"runtime error: {a['error']}")
        time.sleep(0.5)
    raise AssertionError("runtime not ready in time")


def _chat(c: httpx.Client, cid: str, text: str, *, stop_after_tokens: int | None = None) -> dict:
    out = {"text": "", "events": [], "sources": [], "done": None, "error": None, "first_token_s": None}
    t0 = time.monotonic()
    n = 0
    with c.stream("POST", f"/conversations/{cid}/messages", json={"content": text}, timeout=600) as r:
        assert r.status_code == 200, r.read()
        for evt, data in _sse_events(r):
            out["events"].append(evt)
            if evt == "token":
                n += 1
                if out["first_token_s"] is None:
                    out["first_token_s"] = round(time.monotonic() - t0, 3)
                out["text"] += data["text"]
                if stop_after_tokens and n >= stop_after_tokens:
                    with c.stream("POST", f"/conversations/{cid}/stop") as _s:
                        pass
                    with httpx.Client(verify=False, trust_env=False, base_url=c.base_url, headers=c.headers, timeout=30) as c2:
                        c2.post(f"/conversations/{cid}/stop")
                    stop_after_tokens = None
            elif evt == "sources":
                out["sources"] = data["sources"]
            elif evt == "done":
                out["done"] = data["message"]
            elif evt == "error":
                out["error"] = data
    out["wall_s"] = round(time.monotonic() - t0, 2)
    return out


def _wait_ingest_idle(c: httpx.Client, timeout: float = 600) -> None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if c.get("/memory/overview").json()["pending_ingest"] == 0:
            return
        time.sleep(1.0)
    raise AssertionError("ingest queue did not drain")


def _process_mem_mb(pid: int) -> float | None:
    out = subprocess.run(["tasklist", "/FI", f"PID eq {pid}", "/FO", "CSV", "/NH"], capture_output=True).stdout.decode(
        "utf-8", errors="replace"
    )
    m = re.search(r'"([\d.,]+) K"', out)
    if not m:
        return None
    return round(int(re.sub(r"[.,]", "", m.group(1))) / 1024, 1)


def _llama_pids(core_pid: int | None = None) -> list[int]:
    """llama-server processes; with ``core_pid`` only those started by that core
    (other SpAIder instances or measurements may run on the same machine)."""
    if core_pid is not None:
        try:
            import psutil

            return [
                c.pid for c in psutil.Process(core_pid).children(recursive=True) if c.name().lower() == "llama-server.exe"
            ]
        except Exception:  # noqa: BLE001
            return []
    out = subprocess.run(
        ["tasklist", "/FI", "IMAGENAME eq llama-server.exe", "/FO", "CSV", "/NH"], capture_output=True
    ).stdout.decode("utf-8", errors="replace")
    return [int(m.group(1)) for m in re.finditer(r'"llama-server.exe","(\d+)"', out)]


def _pids_alive(pids: list[int]) -> list[int]:
    alive = set(_llama_pids())
    return [p for p in pids if p in alive]


@requires_real()
def test_real_end_to_end(tmp_path):
    chat_src = Path(CHAT_MODEL)
    embed_src = Path(EMBED_MODEL) if EMBED_MODEL else None
    app_root = tmp_path / "SpAIder Töst Ü" / "SpAIder"
    app_root.mkdir(parents=True)
    paths = PortablePaths.from_app_root(app_root)
    paths.ensure()

    # ---- prepare: install the already downloaded files like the download job would
    cat = load_catalog()
    db = Database(paths.db_path)
    db.open()
    reg = ModelRegistry(db, paths)
    chat_entry = cat.get("qwen3-0.6b-q8_0")
    part = paths.downloads_dir / "prep" / chat_entry.files[0].path
    part.parent.mkdir(parents=True)
    shutil.copy2(chat_src, part)
    chat_model = reg.install_from_download(chat_entry, part, sha_verified=True)
    embed_model = None
    if embed_src:
        e_entry = cat.get("qwen3-embedding-0.6b-q8_0")
        part2 = paths.downloads_dir / "prep2" / e_entry.files[0].path
        part2.parent.mkdir(parents=True)
        shutil.copy2(embed_src, part2)
        embed_model = reg.install_from_download(e_entry, part2, sha_verified=True)
    db.close()

    # ---- case 1: fresh start, setup, activation with real probe
    core = Core(app_root, label="first").start()
    try:
        with core.client() as c:
            assert c.get("/health").json()["status"] == "ok"
            assert c.get("/setup/status").json()["state"] == "needs_model"
            ids = {m["id"] for m in c.get("/models/installed").json()["models"]}
            assert chat_model["id"] in ids
            t0 = time.monotonic()
            job = c.post("/models/activate", json={"model_id": chat_model["id"]}).json()["job"]
            job = _wait_job(c, job["id"])
            assert job["state"] == "done", job
            activation_s = round(time.monotonic() - t0, 2)
            assert c.get("/setup/status").json()["state"] == "ready"
            active = c.get("/models/active").json()
            assert active["chat"]["id"] == chat_model["id"] and active["runtime_state"] == "ready"
            REPORT["measurements"]["core_start_s"] = core.started_in
            REPORT["measurements"]["chat_model_activation_s"] = activation_s
            REPORT["measurements"]["chat_model_startup_s"] = active["runtime"]["chat"]["startup_seconds"]
            if embed_model:
                job = _wait_job(c, c.post("/models/activate", json={"model_id": embed_model["id"]}).json()["job"]["id"])
                assert job["state"] == "done", job
                ov = c.get("/memory/overview").json()
                assert ov["index"]["vectors"]["enabled"] and ov["index"]["vectors"]["dimensions"] == 1024
            pids = _llama_pids(core.proc.pid)
            REPORT["measurements"]["llama_server_rss_mb"] = {str(p): _process_mem_mb(p) for p in pids}
            REPORT["measurements"]["core_rss_mb"] = _process_mem_mb(core.proc.pid)

            # ---- case 5a: chat A with project facts + a later correction
            conv_a = c.post("/conversations", json={"title": None}).json()
            r1 = _chat(
                c,
                conv_a["id"],
                "Merk dir bitte: Mein Projekt heißt Orion. Der Projektleiter von Orion ist Idris Kane. Das Budget von Orion beträgt 42000 Euro.",  # noqa: E501
            )
            assert r1["done"] and r1["done"]["status"] == "complete" and r1["text"].strip(), r1
            assert "status" in r1["events"] and "message" in r1["events"]
            REPORT["measurements"]["first_answer"] = {
                "usage": r1["done"]["usage"],
                "wall_s": r1["wall_s"],
                "first_token_s": r1["first_token_s"],
                "chars": len(r1["text"]),
            }
            _wait_ingest_idle(c)
            r2 = _chat(
                c,
                conv_a["id"],
                "Korrektur: Der Projektleiter von Orion ist nicht mehr Idris Kane, sondern jetzt Priya Nair.",
            )
            assert r2["done"]["status"] == "complete"
            _wait_ingest_idle(c)
            ov = c.get("/memory/overview").json()
            assert ov["facts"] >= 2, ov
            REPORT["cases"]["memory_after_two_messages"] = ov
            search = c.get("/memory/search", params={"q": "Projektleiter Orion"}).json()["results"]
            assert search, "memory search found nothing"
            REPORT["cases"]["search_projektleiter"] = [
                {"status": s["status"], "excerpt": s["excerpt"][:80]} for s in search[:6]
            ]
            title_a = c.get(f"/conversations/{conv_a['id']}").json()["conversation"]["title"]

            # ---- case 7: model switch failure -> rollback keeps the working model
            fake = write_fake_gguf(tmp_path / "kaputt.gguf", name="Kaputtes Modell", extra_bytes=4096)
            job = _wait_job(c, c.post("/models/import", json={"path": str(fake)}).json()["job"]["id"])
            assert job["state"] == "done", job
            broken_id = job["result_model_id"]
            job = _wait_job(
                c, c.post("/models/activate", json={"model_id": broken_id}).json()["job"]["id"], timeout=300
            )
            assert job["state"] == "failed" and job["error"]["code"] in ("runtime_start_failed", "probe_failed"), job
            REPORT["cases"]["switch_failure"] = {
                "state": job["state"],
                "error": job["error"],
                "phases": [j.get("step") for j in job["journal"]],
            }
            active = _wait_runtime_ready(c)
            assert active["chat"]["id"] == chat_model["id"], "rollback did not restore the previous model"
            assert c.get("/memory/overview").json()["facts"] >= 2  # memory untouched by the failed switch
            r3 = _chat(c, conv_a["id"], "Sag nur: ok")
            assert r3["done"] and r3["done"]["status"] == "complete"
            assert c.delete(f"/models/{broken_id}").json()["ok"]
            assert c.delete(f"/models/{chat_model['id']}").status_code == 409  # active model protected

            # ---- case 8a: stop while streaming
            r4 = _chat(
                c,
                conv_a["id"],
                "Erzähle eine sehr lange Geschichte über einen Drachen, mindestens 800 Wörter.",
                stop_after_tokens=8,
            )
            assert r4["done"] and r4["done"]["status"] == "stopped", r4["done"]
            REPORT["cases"]["stop"] = {"status": r4["done"]["status"], "chars": len(r4["text"]), "wall_s": r4["wall_s"]}
            _wait_ingest_idle(c)

        # ---- case 8b/3: lifecycle: shutdown releases everything
        own_pids = _llama_pids(core.proc.pid)
        REPORT["measurements"]["shutdown_s"] = core.shutdown()
        assert core.proc.returncode == 0, core.proc.returncode
        assert not _pids_alive(own_pids), "llama-server still running after shutdown"
        assert not paths.lock_path.exists()
        assert not Path(str(paths.db_path) + "-wal").exists() or Path(str(paths.db_path) + "-wal").stat().st_size == 0
    except BaseException:
        core.kill()
        _report()
        raise

    # ---- case 3 + 5b: restart, same data, cross-chat recall with the correction
    core = Core(app_root, label="second").start()
    try:
        with core.client() as c:
            convs = c.get("/conversations").json()["conversations"]
            assert [x["id"] for x in convs] == [conv_a["id"]] and convs[0]["message_count"] == 8
            msgs = c.get(f"/conversations/{conv_a['id']}").json()["messages"]
            assert [m["status"] for m in msgs if m["role"] == "assistant"] == [
                "complete",
                "complete",
                "complete",
                "stopped",
            ]
            assert c.get("/setup/status").json()["state"] == "ready"
            _wait_runtime_ready(c)
            conv_b = c.post("/conversations", json={"title": "Chat B"}).json()
            r5 = _chat(c, conv_b["id"], "Wer ist aktuell der Projektleiter von Orion und wie hoch ist das Budget?")
            assert r5["done"]["status"] == "complete"
            srcs = r5["sources"]
            REPORT["cases"]["cross_chat_recall"] = {
                "answer": r5["text"][:400],
                "sources": [
                    {
                        "kind": s["kind"],
                        "status": s["status"],
                        "origin": s["origin"]["title"],
                        "excerpt": s["excerpt"][:90],
                    }
                    for s in srcs
                ],
            }
            assert any("Priya" in s["excerpt"] and s["status"] == "current" for s in srcs), srcs
            assert any(s["origin"]["title"] == title_a for s in srcs)
            assert "Priya" in r5["text"], r5["text"]
            superseded = [s for s in srcs if s["status"] == "superseded"]
            REPORT["cases"]["supersession_fired"] = bool(superseded)

            # ---- case 6: documents, original removed after import
            outside = tmp_path / "außerhalb" / "firmen notiz.txt"
            outside.parent.mkdir()
            outside.write_text(
                "Die Firma Zephyr hat ihren Hauptsitz in Lissabon. Zephyr stellt das Produkt Lumen her.",
                encoding="utf-8",
            )
            with open(outside, "rb") as fh:
                doc = c.post(
                    "/documents",
                    files={"file": (outside.name, fh, "text/plain")},
                    data={"conversation_id": conv_b["id"]},
                ).json()
            assert doc["status"] in ("indexing", "indexed"), doc
            outside.unlink()
            _wait_ingest_idle(c)
            doc = c.get(f"/documents/{doc['id']}").json()
            assert doc["status"] == "indexed", doc
            r6 = _chat(c, conv_b["id"], "Wo hat die Firma Zephyr ihren Hauptsitz?")
            REPORT["cases"]["document_recall"] = {
                "answer": r6["text"][:300],
                "sources": [(s["kind"], s["origin"]["title"]) for s in r6["sources"]],
            }
            assert "Lissabon" in r6["text"], r6["text"]
            assert any(
                s["kind"] in ("document", "fact") and "firmen notiz" in s["origin"]["title"] for s in r6["sources"]
            ), r6["sources"]

            # ---- case 9: traversal / security
            assert c.get("/documents/../../etc").status_code in (404, 422)
            assert c.post("/models/import", json={"path": str(paths.db_path)}).json()["job"]["id"]
            r = c.post("/conversations/" + conv_b["id"] + "/messages", json={"content": "x" * 200001})
            assert r.status_code == 422
        REPORT["measurements"]["shutdown_2_s"] = core.shutdown()
    except BaseException:
        core.kill()
        _report()
        raise

    # ---- case 4: move to a different path (spaces + umlaut) and a different drive letter via subst
    moved_parent = tmp_path / "Neuer Ort é ß"
    moved_parent.mkdir()
    moved_root = moved_parent / "SpAIder Foldable"
    shutil.move(str(app_root), str(moved_root))
    drive = None
    for letter in "ZYXWVU":
        if not Path(f"{letter}:\\").exists():
            r = subprocess.run(["subst", f"{letter}:", str(moved_parent)], capture_output=True, text=True)
            if r.returncode == 0:
                drive = letter
                break
    start_root = Path(f"{drive}:\\SpAIder Foldable") if drive else moved_root
    REPORT["cases"]["move"] = {"from": str(app_root), "to": str(start_root), "subst_drive": drive}
    core = Core(start_root, label="moved").start()
    try:
        with core.client() as c:
            convs = c.get("/conversations").json()["conversations"]
            assert {x["title"] for x in convs} == {title_a, "Chat B"}
            docs = c.get("/documents").json()["documents"]
            assert docs and docs[0]["status"] == "indexed"
            assert c.get(f"/documents/{docs[0]['id']}/text").json()["text"].startswith("Die Firma Zephyr")
            info = c.get("/system/info").json()
            # A ``subst`` drive is resolved by Path.resolve() to its target directory;
            # a real removable drive keeps its letter. Accept both spellings.
            data_dir = info["data_dir"].lower()
            assert data_dir.startswith(str(start_root).lower()) or data_dir.startswith(str(moved_root).lower()), info
            _wait_runtime_ready(c)
            conv_c = c.post("/conversations", json={"title": "Chat C nach Umzug"}).json()
            r7 = _chat(c, conv_c["id"], "Wie heißt mein Projekt und wer leitet es?")
            assert r7["done"]["status"] == "complete" and ("Orion" in r7["text"] or "Priya" in r7["text"]), r7["text"]
            REPORT["cases"]["after_move"] = {"answer": r7["text"][:300], "data_dir": info["data_dir"]}
        own_pids = _llama_pids(core.proc.pid)
        REPORT["measurements"]["shutdown_3_s"] = core.shutdown()
    except BaseException:
        core.kill()
        raise
    finally:
        if drive:
            subprocess.run(["subst", f"{drive}:", "/D"], capture_output=True)
        _report()
    assert not _pids_alive(own_pids)
