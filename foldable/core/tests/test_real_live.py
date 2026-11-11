"""Real live-session check with a vision model (opt-in):

SPAIDER_FOLDABLE_REAL_TESTS=1, SPAIDER_FOLDABLE_RUNTIME_DIR, SPAIDER_FOLDABLE_TEST_VLM_MODEL
(weights .gguf), SPAIDER_FOLDABLE_TEST_VLM_MMPROJ (projector), optional SPAIDER_FOLDABLE_TEST_VLM_CATALOG_ID.

* starts the WinForms test app (own process, synthetic content), creates a live session
  on it, takes a preview snapshot, asks a structure+image question and checks the answer
  against the synthetic content; records capture/observe/image_encode/first-token timings
* asks a second question with use_image="never" (structure only)
* the overlay's own windows are excluded from sources (is_self)
Writes foldable/build/measurements/live-report.json.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import httpx
import pytest

from spaider_foldable.db import Database
from spaider_foldable.models.catalog import load_catalog
from spaider_foldable.models.registry import ModelRegistry
from spaider_foldable.paths import PortablePaths

from .conftest import REAL, RUNTIME_DIR
from .test_real_activity import _send
from .test_real_e2e import Core, _wait_runtime_ready

VLM = os.environ.get("SPAIDER_FOLDABLE_TEST_VLM_MODEL")
MMPROJ = os.environ.get("SPAIDER_FOLDABLE_TEST_VLM_MMPROJ")
CATALOG_ID = os.environ.get("SPAIDER_FOLDABLE_TEST_VLM_CATALOG_ID", "qwen3-vl-4b-instruct-q4_k_m")
APP_PS1 = Path(__file__).parent / "fixtures" / "winforms_testapp.ps1"
REPORT = Path(__file__).resolve().parents[2] / "build" / "measurements" / "live-report.json"

pytestmark = pytest.mark.skipif(not (REAL and RUNTIME_DIR and VLM and MMPROJ), reason="needs real runtime and a vision model with projector")


def _start_testapp(tmp_path: Path, title: str):
    out = tmp_path / "ausgabe.txt"
    proc = subprocess.Popen(["powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", str(APP_PS1), "-OutFile", str(out), "-Title", title, "-AutoCloseSeconds", "600"])
    return proc, out


def _ask_live(c: httpx.Client, sid: str, content: str, use_image: str) -> dict:
    out = {"activity": [], "text": "", "done": None, "observation": None, "t0": time.monotonic(), "first_token_at": None, "thinking_events": 0, "sources": []}
    with c.stream("POST", f"/live/sessions/{sid}/ask", json={"content": content, "use_memory": True, "use_image": use_image}, timeout=600) as r:
        assert r.status_code == 200, r.read()
        ev, data_lines = None, []
        for line in r.iter_lines():
            if line.startswith("event:"):
                ev = line[6:].strip()
            elif line.startswith("data:"):
                data_lines.append(line[5:].strip())
            elif line == "" and ev:
                data = json.loads("\n".join(data_lines) or "{}")
                if ev == "activity":
                    out["activity"].append(data)
                elif ev == "observation":
                    out["observation"] = data
                elif ev == "token":
                    if out["first_token_at"] is None:
                        out["first_token_at"] = time.monotonic()
                    out["text"] += data["text"]
                elif ev == "done":
                    out["done"] = data["message"]
                ev, data_lines = None, []
    out["first_token_s"] = round(out["first_token_at"] - out["t0"], 3) if out["first_token_at"] else None
    out["total_s"] = round(time.monotonic() - out["t0"], 3)
    return out


def test_live_question_with_vision(tmp_path):
    app_root = tmp_path / "Live ä" / "SpAIder"
    app_root.mkdir(parents=True)
    paths = PortablePaths.from_app_root(app_root)
    paths.ensure()
    db = Database(paths.db_path)
    db.open()
    reg = ModelRegistry(db, paths)
    entry = load_catalog().get(CATALOG_ID)
    part = paths.downloads_dir / "prep" / entry.files[0].path
    part.parent.mkdir(parents=True)
    shutil.copy2(Path(VLM), part)
    shutil.copy2(Path(MMPROJ), part.parent / entry.files[1].path)
    model = reg.install_from_download(entry, part, sha_verified=True)
    assert model["mmproj_path"], "projector was not paired"
    reg.set_state("active_chat_model_id", model["id"])
    db.close()

    title = f"SpAIder Testanwendung {int(time.time()) % 10000}"
    app_proc, out_file = _start_testapp(tmp_path, title)
    report: dict = {"model": model["id"]}
    core = Core(app_root, label="live").start()
    try:
        with core.client() as c:
            active = _wait_runtime_ready(c, timeout=600)
            report["runtime"] = active["runtime"]["chat"]["options"]
            caps = c.get("/agent/capabilities").json()
            assert caps["model"]["vision_input"] is True, caps["model"]
            report["capabilities"] = caps["model"]
            # the test app must be visible to the source list
            src = None
            for _ in range(60):
                srcs = c.get("/live/sources").json()
                src = next((w for w in srcs["windows"] if w["title"] == title), None)
                if src:
                    break
                time.sleep(0.5)
            assert src, "test app window not listed"
            assert not any(w["is_self"] for w in srcs["windows"])
            report["sources"] = {"windows": len(srcs["windows"]), "screens": len(srcs["screens"]), "desktop_interactive": srcs["desktop_interactive"]}
            # fill the app with synthetic text through UIA (so the question has content to read)
            import asyncio

            from spaider_foldable.agent.broker import Broker, ToolContext
            from spaider_foldable.agent.models import Scopes, WindowsScope
            from spaider_foldable.agent.tools import windows as windows_tools
            from spaider_foldable.observe import windows as W
            from spaider_foldable.observe.snapshot import SnapshotTaker
            from spaider_foldable.observe.uia import UiaProvider

            uia = UiaProvider()
            taker = SnapshotTaker(uia)
            snaps: dict = {}
            broker = Broker()
            windows_tools.register(broker, uia=uia, take_snapshot=lambda tid, s, want_image=False: snaps.__setitem__((tid, s.id), taker.take(tid, s, want_image=want_image)) or snaps[(tid, s.id)], get_snapshot=lambda tid, sid: snaps.get((tid, sid)), resolve_source=lambda sid: next(w for w in W.list_windows() if w.id == sid), remember_names=lambda tid, m: None)
            ctx = ToolContext(task_id="t", scopes=Scopes(windows=WindowsScope(source_ids=[src["id"]], allowed_processes=[])), cancel_generation=0, is_cancelled=lambda: False, latest_snapshot_ids={}, emit=lambda k, p: None)
            asyncio.run(broker.get("windows.inspect").run({"source_id": src["id"]}, ctx))
            snap = snaps[("t", src["id"])]
            name_el = next(e for e in snap.tree.elements if e.name == "Name" and e.role == "Edit")
            notes_el = next(e for e in snap.tree.elements if e.name == "Notizen" and e.role == "Edit")
            asyncio.run(broker.get("windows.set_value").run({"source_id": src["id"], "element_id": name_el.id, "text": "Projekt Nordlicht"}, ctx))
            asyncio.run(broker.get("windows.set_value").run({"source_id": src["id"], "element_id": notes_el.id, "text": "Kickoff am 12.11.2026, Budget 48500 Euro"}, ctx))

            sess = c.post("/live/sessions", json={"source_id": src["id"]}).json()["session"]
            assert sess["capture_mode"] == "window" and sess["vision_available"] is True
            t0 = time.monotonic()
            snap_api = c.post(f"/live/sessions/{sess['id']}/snapshot", json={"preview": True, "max_elements": 100}).json()["snapshot"]
            report["snapshot"] = {"ms": int((time.monotonic() - t0) * 1000), "quality": snap_api["quality"], "elements": len(snap_api["elements"]), "text_chars": snap_api["text_chars"], "preview_bytes": len(snap_api["image"]["preview_png_base64"] or "") if snap_api["image"] else 0}
            assert snap_api["quality"] == "ok" and snap_api["image"]["preview_png_base64"]
            assert "Nordlicht" in snap_api["text_excerpt"]

            r1 = _ask_live(c, sess["id"], "Was steht im Feld Name und welches Budget wird in den Notizen genannt? Antworte kurz.", "always")
            phases = {e["phase"]: e for e in r1["activity"] if e["state"] == "done"}
            report["question_image"] = {"first_token_s": r1["first_token_s"], "total_s": r1["total_s"], "observation": r1["observation"], "answer": r1["text"][:300], "capture_ms": phases.get("capture", {}).get("counters", {}).get("capture_ms"), "image_encode": phases.get("image_encode", {}).get("counters"), "timings": (r1["done"] or {}).get("timings")}
            assert r1["observation"]["used_image"] is True
            assert "Nordlicht" in r1["text"] and ("48500" in r1["text"] or "48.500" in r1["text"]), r1["text"]

            r2 = _ask_live(c, sess["id"], "Welche Schaltflächen gibt es in diesem Fenster? Nur die Namen.", "never")
            report["question_structure"] = {"first_token_s": r2["first_token_s"], "total_s": r2["total_s"], "observation": r2["observation"], "answer": r2["text"][:300]}
            assert r2["observation"]["used_image"] is False and "Speichern" in r2["text"]

            # live and main chat share the conversation
            msgs = c.get(f"/conversations/{sess['conversation_id']}").json()["messages"]
            assert len(msgs) == 4
            c.delete(f"/live/sessions/{sess['id']}")
            assert c.get(f"/live/sessions/{sess['id']}").status_code == 404
        core.shutdown()
    except BaseException:
        core.kill()
        raise
    finally:
        app_proc.terminate()
        REPORT.parent.mkdir(parents=True, exist_ok=True)
        REPORT.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
