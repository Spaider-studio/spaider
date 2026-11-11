"""Model-independence run with real weights of other families (opt-in).

Set ``SPAIDER_FOLDABLE_TEST_FAMILY_MODELS`` to a ``;``-separated list of GGUF
files (chat models of families other than the one used by the other real
tests). For every file the test

* imports it over the public API (path C: local import, header + chat
  template + capabilities detection, no family-specific code path),
* activates it and records the planned runtime options (backend, threads),
* runs the same memory scenario as the activity test (fact, correction,
  immediate question in a second conversation while post-processing is still
  pending), the stop-latency check and a calibration job,
* records first-token latency, generation speed and whether the shipped
  template exposes a thinking switch, into
  ``foldable/build/measurements/families-report.json``.

Assertions cover the model-independent parts (API contract, activity events,
memory visibility, stop latency). The answer text itself is recorded, not
asserted: whether a 1B model names the corrected person is a model property.
"""

from __future__ import annotations

import json
import os
import shutil
import time
from pathlib import Path

import httpx
import pytest

from spaider_foldable.db import Database
from spaider_foldable.paths import PortablePaths

from .conftest import REAL, RUNTIME_DIR
from .test_real_activity import _send
from .test_real_e2e import Core, _wait_job, _wait_runtime_ready

FAMILY_MODELS = [p for p in os.environ.get("SPAIDER_FOLDABLE_TEST_FAMILY_MODELS", "").split(";") if p.strip()]
REPORT_PATH = Path(__file__).resolve().parents[2] / "build" / "measurements" / "families-report.json"

pytestmark = pytest.mark.skipif(
    not (REAL and RUNTIME_DIR and FAMILY_MODELS),
    reason="set SPAIDER_FOLDABLE_REAL_TESTS=1, SPAIDER_FOLDABLE_RUNTIME_DIR and SPAIDER_FOLDABLE_TEST_FAMILY_MODELS",
)


def _stage_import_file(src: Path, stage_dir: Path) -> Path:
    """Expose a (possibly ``.part``-named) GGUF under its proper name outside the data dir."""
    name = src.name[:-5] if src.name.endswith(".part") else src.name
    dst = stage_dir / name
    stage_dir.mkdir(parents=True, exist_ok=True)
    try:
        os.link(src, dst)
    except OSError:
        shutil.copy2(src, dst)
    return dst


def _load_report() -> dict:
    if REPORT_PATH.exists():
        try:
            return json.loads(REPORT_PATH.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            pass
    return {"models": {}}


def _save_report(rep: dict) -> None:
    REPORT_PATH.parent.mkdir(parents=True, exist_ok=True)
    REPORT_PATH.write_text(json.dumps(rep, indent=2, ensure_ascii=False), encoding="utf-8")


def _first_token_s(r: dict) -> float | None:
    t = (r.get("done") or {}).get("timings") or {}
    return round(t["first_token_ms"] / 1000, 3) if t.get("first_token_ms") is not None else None


@pytest.mark.parametrize("model_file", FAMILY_MODELS, ids=[Path(p).name for p in FAMILY_MODELS])
def test_family_model_end_to_end(tmp_path, model_file):
    src = Path(model_file)
    assert src.is_file(), src
    app_root = tmp_path / "Familie ä" / "SpAIder"
    app_root.mkdir(parents=True)
    staged = _stage_import_file(src, tmp_path / "import")
    paths = PortablePaths.from_app_root(app_root)
    paths.ensure()
    rec: dict = {"file": src.name, "size_bytes": src.stat().st_size}

    core = Core(app_root, label=f"family-{src.stem[:12]}").start()
    try:
        with core.client() as c:
            # path C: local import through the API (validation, header, template, metadata)
            job = c.post("/models/import", json={"path": str(staged)}).json()["job"]
            job = _wait_job(c, job["id"], timeout=900)
            assert job["state"] == "done", job
            installed = c.get("/models/installed").json()["models"]
            model = next(m for m in installed if m["role"] == "chat")
            caps = model["capabilities"]
            rec.update(
                {
                    "model_id": model["id"],
                    "name": model["name"],
                    "architecture": model["architecture"],
                    "context_length": model["context_length"],
                    "family": caps["family"],
                    "type": caps["type"],
                    "chat_template": caps["chat_template"],
                    "reasoning_toggle": caps["reasoning_toggle"],
                    "system_role": caps["system_role"],
                    "verification": model.get("verification"),
                }
            )
            assert caps["type"] == "chat", caps
            assert caps["chat_template"] is True, "chat template missing in GGUF header"

            job = c.post("/models/activate", json={"model_id": model["id"]}).json()["job"]
            job = _wait_job(c, job["id"], timeout=900)
            assert job["state"] == "done", job
            active = _wait_runtime_ready(c, timeout=600)
            rec["runtime"] = active["runtime"]["chat"]

            conv = c.post("/conversations", json={"title": "Familie"}).json()
            r1 = _send(c, conv["id"], "Merk dir: Die Projektleiterin von Polarstern ist Lena Beispiel.")
            assert r1["done"]["status"] == "complete", r1["done"]
            phases = [e["phase"] for e in r1["activity"]]
            for ph in ("memory_search", "prompt_build", "generating", "answer_done", "saved", "postprocess_pending"):
                assert ph in phases, (ph, phases)
            rec["turn1"] = {
                "first_token_s": _first_token_s(r1),
                "timings": r1["done"]["timings"],
                "text": r1["text"][:160],
                "thinking_events": r1["thinking_events"],
            }

            r2 = _send(
                c,
                conv["id"],
                "Korrektur: Die Projektleiterin von Polarstern ist jetzt Mara Muster, nicht mehr Lena Beispiel.",
            )
            assert r2["done"]["status"] == "complete"
            conv_b = c.post("/conversations", json={"title": "Sofortfrage"}).json()
            r3 = _send(c, conv_b["id"], "Wer ist aktuell die Projektleiterin von Polarstern? Antworte nur mit dem Namen.")
            assert any("Mara Muster" in s["excerpt"] for s in r3["sources"]), r3["sources"]
            rec["immediate_question"] = {
                "first_token_s": _first_token_s(r3),
                "timings": r3["done"]["timings"],
                "answer": r3["text"][:160],
                "answer_names_new_lead": "Mara" in r3["text"],
                "answer_names_old_lead": "Lena" in r3["text"],
                "pending_sources_seen": sum(1 for s in r3["sources"] if s.get("processing") == "pending"),
                "preemptions": c.get("/models/active").json()["scheduler"]["preemptions"],
            }

            r4 = _send(
                c,
                conv_b["id"],
                "Erzähle eine sehr lange Geschichte über das Meer, mindestens 800 Wörter.",
                stop_after=6,
            )
            assert r4["done"]["status"] == "stopped", r4["done"]
            assert any(e["state"] == "cancelled" for e in r4["activity"])
            rec["stop_to_done_s"] = round(r4["done_at"] - r4["stop_sent_at"], 3)
            assert rec["stop_to_done_s"] < 5.0

            # measured profile via the public calibration job
            job = c.post(f"/models/{model['id']}/calibrate").json()["job"]
            job = _wait_job(c, job["id"], timeout=600)
            rec["calibrate"] = {"state": job["state"], "message_de": job.get("message_de")}
            if job["state"] == "done":
                m = next(m for m in c.get("/models/installed").json()["models"] if m["id"] == model["id"])
                rec["measured"] = m.get("measured")

            # the raw fact must become enriched (or stay a raw fact) without error
            deadline = time.monotonic() + 600
            while time.monotonic() < deadline and c.get("/memory/overview").json()["pending_ingest"] > 0:
                time.sleep(2)
            ov = c.get("/memory/overview").json()
            rec["memory_overview_after_drain"] = {k: ov[k] for k in ("facts", "entities", "pending_ingest")}
        rec["shutdown_s"] = core.shutdown()
    except BaseException:
        core.kill()
        raise
    finally:
        db = Database(paths.db_path)
        db.open()
        try:
            rec["raw_facts_left"] = int(db.scalar("SELECT count(*) FROM nodes WHERE type='FACT' AND processing='raw'") or 0)
            rec["failed_queue_items"] = int(db.scalar("SELECT count(*) FROM ingest_queue WHERE state='failed'") or 0)
        finally:
            db.close()
        rep = _load_report()
        rep.setdefault("models", {})[src.name] = rec
        rep["runtime_dir"] = RUNTIME_DIR
        rep["backend_env"] = os.environ.get("SPAIDER_FOLDABLE_BACKEND")
        _save_report(rep)
