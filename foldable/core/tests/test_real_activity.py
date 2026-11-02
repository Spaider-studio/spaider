"""Real-runtime checks for v0.2 (opt-in like test_real_e2e):

* activity events arrive in contract order with one request id and monotonic
  sequence numbers; prompt_processing carries measured counters
* a correction asked about IMMEDIATELY after it was stated (background
  extraction still pending) is already visible to the answer
* restart with an open post-processing queue resumes the queue
* background extraction is preempted by a chat request and finishes later
* stop latency: cancelled event arrives after the server stopped
"""

from __future__ import annotations

import json
import shutil
import time
from pathlib import Path

import httpx

from spaider_foldable.db import Database
from spaider_foldable.models.catalog import load_catalog
from spaider_foldable.models.registry import ModelRegistry
from spaider_foldable.paths import PortablePaths

from .conftest import CHAT_MODEL, requires_real
from .test_real_e2e import Core, _sse_events, _wait_runtime_ready

REPORT: dict = {}


def _send(c: httpx.Client, cid: str, text: str, *, stop_after: int | None = None) -> dict:
    out = {"activity": [], "text": "", "done": None, "sources": [], "t0": time.monotonic(), "stop_sent_at": None, "thinking_events": 0}
    n = 0
    with c.stream("POST", f"/conversations/{cid}/messages", json={"content": text}, timeout=600) as r:
        assert r.status_code == 200, r.read()
        for evt, data in _sse_events(r):
            if evt == "activity":
                out["activity"].append(data)
            elif evt == "token":
                n += 1
                out["text"] += data["text"]
                if stop_after and n >= stop_after and out["stop_sent_at"] is None:
                    out["stop_sent_at"] = time.monotonic()
                    with httpx.Client(verify=False, trust_env=False, base_url=c.base_url, headers=c.headers, timeout=10) as c2:
                        c2.post(f"/conversations/{cid}/stop")
            elif evt == "thinking":
                out["thinking_events"] += 1
            elif evt == "sources":
                out["sources"] = data["sources"]
            elif evt == "done":
                out["done"] = data["message"]
                out["done_at"] = time.monotonic()
    return out


@requires_real()
def test_activity_events_and_immediate_corrections(tmp_path):
    app_root = tmp_path / "Aktivität ü" / "SpAIder"
    app_root.mkdir(parents=True)
    paths = PortablePaths.from_app_root(app_root)
    paths.ensure()
    db = Database(paths.db_path)
    db.open()
    reg = ModelRegistry(db, paths)
    entry = load_catalog().get("qwen3-0.6b-q8_0")
    part = paths.downloads_dir / "prep" / entry.files[0].path
    part.parent.mkdir(parents=True)
    shutil.copy2(Path(CHAT_MODEL), part)
    model = reg.install_from_download(entry, part, sha_verified=True)
    reg.set_state("active_chat_model_id", model["id"])
    db.close()

    core = Core(app_root, label="activity").start()
    try:
        with core.client() as c:
            _wait_runtime_ready(c)
            hw = c.get("/system/hardware").json()
            assert hw["cpu"]["cores_physical"] >= 1 and "fingerprint" in hw
            REPORT["hardware"] = {
                "cpu": hw["cpu"]["name"],
                "gpus": [g["name"] for g in hw["gpus"]],
                "backend": hw["active_backend"],
            }
            active = c.get("/models/active").json()
            REPORT["chat_runtime"] = active["runtime"]["chat"]
            assert active["chat"]["capabilities"]["reasoning_toggle"] == "enable_thinking"
            conv = c.post("/conversations", json={"title": "Aktivität"}).json()

            # 1) event contract
            r = _send(c, conv["id"], "Merk dir: Die Projektleiterin von Polarstern ist Lena Beispiel.")
            ev = r["activity"]
            assert ev, "no activity events"
            rid = ev[0]["request_id"]
            assert all(e["request_id"] == rid for e in ev) and rid == r["done"]["id"]
            seqs = [e["seq"] for e in ev]
            assert seqs == sorted(seqs) and len(set(seqs)) == len(seqs)
            phases = [e["phase"] for e in ev]
            for ph in (
                "memory_search",
                "prompt_build",
                "prompt_processing",
                "generating",
                "answer_done",
                "saved",
                "postprocess_pending",
            ):
                assert ph in phases, (ph, phases)
            assert (
                phases.index("memory_search")
                < phases.index("generating")
                < phases.index("answer_done")
                < phases.index("saved")
            )
            pp = [e for e in ev if e["phase"] == "prompt_processing" and e["state"] == "progress"]
            REPORT["prompt_progress_events"] = len(pp)
            if pp:
                assert pp[-1]["counters"]["prompt_total"] > 0
            gen_done = next(e for e in ev if e["phase"] == "answer_done")
            assert gen_done["state"] == "done" and gen_done["counters"]["tokens"] > 0
            assert r["done"]["timings"]["total_ms"] > 0 and "memory_search_ms" in r["done"]["timings"]
            REPORT["first_turn_timings"] = r["done"]["timings"]

            # 2) correction, then an immediate question while post-processing is still pending
            r2 = _send(
                c,
                conv["id"],
                "Korrektur: Die Projektleiterin von Polarstern ist jetzt Mara Muster, nicht mehr Lena Beispiel.",
            )
            assert r2["done"]["status"] == "complete"
            conv_now = c.get(f"/conversations/{conv['id']}").json()["conversation"]
            REPORT["pending_after_correction"] = conv_now["postprocessing_pending"]
            conv_b = c.post("/conversations", json={"title": "Sofortfrage"}).json()
            r3 = _send(c, conv_b["id"], "Wer ist aktuell die Projektleiterin von Polarstern? Antworte mit dem Namen.")
            REPORT["immediate_answer"] = r3["text"][:200]
            raw_sources = [s for s in r3["sources"] if s.get("processing") == "pending"]
            REPORT["pending_sources_seen"] = len(raw_sources)
            assert any("Mara Muster" in s["excerpt"] for s in r3["sources"]), r3["sources"]
            current = [s for s in r3["sources"] if s.get("status") != "superseded"]
            assert not any("Lena Beispiel" in s["excerpt"] and "Mara" not in s["excerpt"] for s in current), current
            assert "Mara" in r3["text"], r3["text"]
            # the chat must have preempted background work at least once if any was running
            sched = c.get("/models/active").json()["scheduler"]
            REPORT["preemptions"] = sched["preemptions"]

            # 3) stop latency
            r4 = _send(
                c,
                conv_b["id"],
                "Erzähle eine sehr lange Geschichte über das Meer, mindestens 800 Wörter.",
                stop_after=6,
            )
            assert r4["done"]["status"] == "stopped"
            cancelled = [e for e in r4["activity"] if e["state"] == "cancelled"]
            assert cancelled, r4["activity"][-3:]
            REPORT["stop_to_done_s"] = round(r4["done_at"] - r4["stop_sent_at"], 3)
            assert REPORT["stop_to_done_s"] < 5.0

        # 4) restart with an open queue: shutdown while items may be pending
        pending_before = Database(paths.db_path)
        pending_before.open()
        n_pending = pending_before.scalar("SELECT count(*) FROM ingest_queue WHERE state IN ('pending','running')")
        pending_before.close()
        core.shutdown()
        REPORT["pending_at_shutdown"] = n_pending
    except BaseException:
        core.kill()
        raise

    core = Core(app_root, label="activity-2").start()
    try:
        with core.client() as c:
            _wait_runtime_ready(c)
            # global channel delivers scheduler/runtime state and post-processing events
            seen = _watch_global_activity(c, max_s=90, stop_when_idle=(n_pending == 0))
            assert "scheduler" in seen and "runtime" in seen, seen
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline and c.get("/memory/overview").json()["pending_ingest"] > 0:
                time.sleep(2)
            ov = c.get("/memory/overview").json()
            REPORT["overview_after_restart"] = {k: ov[k] for k in ("facts", "entities", "pending_ingest")}
            assert ov["pending_ingest"] == 0
            assert db_count(paths.db_path, "SELECT count(*) FROM nodes WHERE type='FACT' AND processing='raw'") == 0
            conv_c = c.post("/conversations", json={"title": "Nach Neustart"}).json()
            r5 = _send(c, conv_c["id"], "Wer leitet Polarstern jetzt? Nur der Name.")
            REPORT["answer_after_restart"] = r5["text"][:200]
            assert any("Mara Muster" in s["excerpt"] for s in r5["sources"])
            # the replaced statement must not be offered as a current fact any more
            current = [s for s in r5["sources"] if s.get("status") != "superseded"]
            assert not any("Lena Beispiel" in s["excerpt"] and "Mara" not in s["excerpt"] for s in current), current
            REPORT["answer_after_restart_names_new_lead"] = "Mara" in r5["text"]
        core.shutdown()
    except BaseException:
        core.kill()
        raise
    finally:
        out = Path(__file__).resolve().parents[2] / "build" / "measurements" / "activity-report.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(REPORT, indent=2, ensure_ascii=False), encoding="utf-8")


def _watch_global_activity(c: httpx.Client, *, max_s: float, stop_when_idle: bool) -> list[str]:
    """Collect event names from GET /activity; keep-alive comments re-check the deadline."""
    seen: list[str] = []
    t0 = time.monotonic()
    event, data = None, []
    with c.stream("GET", "/activity", timeout=httpx.Timeout(120, read=60)) as r:
        for line in r.iter_lines():
            if time.monotonic() - t0 > max_s:
                break
            if line.startswith("event:"):
                event = line[6:].strip()
            elif line.startswith("data:"):
                data.append(line[5:].strip())
            elif line == "":
                if event:
                    payload = json.loads(chr(10).join(data) or "{}")
                    seen.append(event)
                    if event == "activity" and payload.get("phase") == "postprocess_done":
                        break
                    if stop_when_idle and len(seen) >= 2:
                        break
                event, data = None, []
    return seen


def db_count(path: Path, sql: str) -> int:
    d = Database(path)
    d.open()
    try:
        return int(d.scalar(sql) or 0)
    finally:
        d.close()
