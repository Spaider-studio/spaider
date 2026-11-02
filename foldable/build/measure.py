"""Performance measurement for SpAIder Foldable: direct runtime vs. full app path.

Measures, with the same prompts and settings:
  direct   llama-server alone (LlamaServerProcess + LocalLLMClient): model load,
           time to first visible token, tokens/s, prompt tokens/s, CPU time and
           peak RSS of the server process
  app      the core process over its HTTP API (fresh app root with the model
           installed): per-phase timings from the SSE events (retrieving,
           sources, generating, first token, done), server usage, CPU time and
           peak RSS of core + llama-server; cold (empty memory) and warm (after
           a few stored facts); stop latency (POST /stop -> done event, and
           llama-server CPU time flattening)

No private data is used: prompts are synthetic. Output: JSON + a Markdown
summary with medians, min/max (p95 only with >= 20 repetitions).

Usage:
  python foldable/build/measure.py --model <gguf> --label qwen3-4b-cpu --out <dir> [--runtime <dir>]
         [--reps 3] [--gpu-layers N] [--modes direct,app] [--threads N]
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import shutil
import statistics
import subprocess
import sys
import time
from pathlib import Path

import httpx

try:
    import psutil
except ImportError:  # pragma: no cover
    psutil = None

HERE = Path(__file__).resolve().parent
CORE_DIR = HERE.parent / "core"
sys.path.insert(0, str(CORE_DIR))

from spaider_foldable.bootstrap import bootstrap  # noqa: E402

bootstrap()

from spaider_foldable.chat.prompts import SYSTEM_PROMPT  # noqa: E402
from spaider_foldable.db import Database  # noqa: E402
from spaider_foldable.inference.client import LocalLLMClient  # noqa: E402
from spaider_foldable.inference.runtime import LlamaServerProcess  # noqa: E402
from spaider_foldable.models.registry import ModelRegistry  # noqa: E402
from spaider_foldable.paths import PortablePaths  # noqa: E402

SHORT_PROMPT = "Nenne drei Vorteile von Sonnenenergie in je einem Satz."
LONG_FILLER = (
    "Die fiktive Firma Nordwind Energie plant für das kommende Jahr drei Projekte. Das erste Projekt betrifft "
    "den Ausbau einer Solaranlage auf dem Werksgelände in Lüneburg mit einer geplanten Leistung von zwei "
    "Megawatt. Das zweite Projekt ist die Modernisierung der Leitwarte, bei der die Software für die "
    "Fernüberwachung ersetzt wird; dafür wurde ein Budget von vierhunderttausend Euro genehmigt. Das dritte "
    "Projekt ist eine Machbarkeitsstudie für einen Batteriespeicher mit zehn Megawattstunden Kapazität. "
    "Die Projektleitung liegt bei Frau Beispiel, die technische Beratung übernimmt ein externes Büro. "
    "Alle Projekte sollen bis zum Jahresende abgeschlossen sein, wobei die Studie bereits im Sommer "
    "vorliegen soll. Risiken sind Lieferengpässe bei Wechselrichtern und Genehmigungsverfahren. "
)
LONG_PROMPT = (
    "Hier ist ein Hintergrundtext:\n\n" + LONG_FILLER * 4 + "\n\nFasse die drei Projekte in je einem Satz zusammen."
)
WARMUP_FACTS = [
    "Merk dir: Mein Lieblingsprojekt heißt Polarstern. Die Projektleiterin von Polarstern ist Lena Beispiel.",
    "Das Budget von Polarstern beträgt 120000 Euro und der Starttermin ist der 1. März.",
    "Korrektur: Die Projektleiterin von Polarstern ist jetzt Mara Muster.",
]
TOKEN = "measure-token-" + "m" * 24


def pctl(vals: list[float], p: float) -> float | None:
    if len(vals) < 20:
        return None
    vals = sorted(vals)
    k = (len(vals) - 1) * p
    f, c = int(k), min(int(k) + 1, len(vals) - 1)
    return vals[f] + (vals[c] - vals[f]) * (k - f)


def summarize(samples: list[dict], key: str) -> dict:
    vals = [s[key] for s in samples if s.get(key) is not None]
    if not vals:
        return {"n": 0}
    return {
        "n": len(vals),
        "median": round(statistics.median(vals), 3),
        "min": round(min(vals), 3),
        "max": round(max(vals), 3),
        "p95": pctl(vals, 0.95),
    }


class ProcWatch:
    """Samples CPU time and peak RSS of a set of pids while a block runs."""

    def __init__(self, pids: list[int]):
        self.procs = [psutil.Process(p) for p in pids] if psutil else []
        self.start_cpu = {p.pid: sum(p.cpu_times()[:2]) for p in self.procs}
        self.peak_rss = {p.pid: 0 for p in self.procs}

    def sample(self) -> None:
        for p in self.procs:
            try:
                self.peak_rss[p.pid] = max(self.peak_rss[p.pid], p.memory_info().rss)
            except psutil.Error:
                pass

    def result(self) -> dict:
        out = {}
        for p in self.procs:
            try:
                cpu = sum(p.cpu_times()[:2]) - self.start_cpu[p.pid]
            except psutil.Error:
                cpu = None
            out[str(p.pid)] = {"name": p.name() if p.is_running() else "?", "cpu_s": round(cpu, 2) if cpu is not None else None,
                               "peak_rss_mb": round(self.peak_rss[p.pid] / 2**20, 1)}
        return out


async def measure_direct(model: Path, runtime: Path, reps: int, gpu_layers: int | None, threads: int | None, logs: Path) -> dict:
    extra = []
    if gpu_layers is not None:
        extra += ["-ngl", str(gpu_layers)]
    t0 = time.monotonic()
    proc = LlamaServerProcess(runtime_dir=runtime, model_path=model, role="chat", log_path=logs / "measure-direct.log",
                              context_tokens=8192, threads=threads, extra_args=extra)
    proc.start()
    proc.wait_ready(300)
    load_s = round(time.monotonic() - t0, 2)
    client = LocalLLMClient(proc.base_url, proc.api_key)
    samples: list[dict] = []
    sysinfo_line = ""
    try:
        for kind, prompt in (("short", SHORT_PROMPT), ("long", LONG_PROMPT)):
            for i in range(reps + 1):  # first = cold for this prompt kind
                watch = ProcWatch([proc.pid]) if proc.pid else None
                t1 = time.monotonic()
                first = None
                text = ""
                usage: dict = {}
                async for ch in client.stream_chat([{"role": "system", "content": SYSTEM_PROMPT}, {"role": "user", "content": prompt}],
                                                   temperature=0.0, max_tokens=120, enable_thinking=False):
                    if ch.done:
                        usage = ch.usage
                        break
                    if ch.text:
                        if first is None:
                            first = time.monotonic() - t1
                        text += ch.text
                    if watch:
                        watch.sample()
                samples.append({
                    "mode": "direct", "kind": kind, "cold": i == 0, "ttft_s": round(first, 3) if first else None,
                    "wall_s": round(time.monotonic() - t1, 3), "tps": usage.get("tokens_per_second"),
                    "prompt_tps": usage.get("prompt_tokens_per_second"), "prompt_tokens": usage.get("prompt_tokens"),
                    "completion_tokens": usage.get("completion_tokens"), "chars": len(text),
                    "proc": watch.result() if watch else {},
                })
    finally:
        proc.stop()
    try:
        for line in (logs / "measure-direct.log").read_text(encoding="utf-8", errors="replace").splitlines():
            if "system_info" in line or "n_threads" in line:
                sysinfo_line = line[-200:]
                break
    except OSError:
        pass
    return {"load_s": load_s, "samples": samples, "runtime_info": sysinfo_line}


def _sse(resp: httpx.Response):
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


class CoreProc:
    def __init__(self, app_root: Path, runtime: Path, env_extra: dict | None = None):
        self.app_root = app_root
        self.runtime = runtime
        self.env_extra = env_extra or {}
        self.proc = None
        self.port = None

    def start(self) -> float:
        env = dict(os.environ)
        env.update({"SPAIDER_FOLDABLE_TOKEN": TOKEN, "SPAIDER_FOLDABLE_RUNTIME_DIR": str(self.runtime), "PYTHONIOENCODING": "utf-8"})
        env.update(self.env_extra)
        t0 = time.monotonic()
        self.proc = subprocess.Popen([sys.executable, "-m", "spaider_foldable", "--app-root", str(self.app_root)], cwd=str(CORE_DIR),
                                     env=env, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True, encoding="utf-8")
        while True:
            line = self.proc.stdout.readline()
            if not line:
                raise RuntimeError("core exited")
            try:
                evt = json.loads(line)
            except ValueError:
                continue
            if evt.get("event") == "ready":
                self.port = int(evt["port"])
                return round(time.monotonic() - t0, 2)
            if evt.get("event") == "error":
                raise RuntimeError(str(evt))

    def client(self) -> httpx.Client:
        return httpx.Client(base_url=f"http://127.0.0.1:{self.port}/api", headers={"X-SpAIder-Token": TOKEN}, timeout=600)

    def llama_pids(self) -> list[int]:
        if not psutil:
            return []
        return [p.pid for p in psutil.process_iter(["name"]) if (p.info["name"] or "").lower().startswith("llama-server")]

    def shutdown(self) -> float:
        t0 = time.monotonic()
        with self.client() as c:
            c.post("/system/shutdown")
        self.proc.wait(timeout=120)
        return round(time.monotonic() - t0, 2)


def wait_ready(c: httpx.Client, timeout: float = 300) -> dict:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        a = c.get("/models/active").json()
        if a["runtime_state"] == "ready":
            return a
        if a["runtime_state"] == "error":
            raise RuntimeError(a["error"])
        time.sleep(0.3)
    raise RuntimeError("runtime not ready")


def wait_idle(c: httpx.Client, timeout: float = 900) -> float:
    t0 = time.monotonic()
    while time.monotonic() - t0 < timeout:
        if c.get("/memory/overview").json()["pending_ingest"] == 0:
            return round(time.monotonic() - t0, 2)
        time.sleep(1)
    return -1


def app_turn(c: httpx.Client, cid: str, prompt: str, pids: list[int], stop_after: int | None = None) -> dict:
    watch = ProcWatch(pids) if psutil else None
    t0 = time.monotonic()
    marks: dict = {}
    text = ""
    n = 0
    done = None
    stop_t = None
    with c.stream("POST", f"/conversations/{cid}/messages", json={"content": prompt}) as r:
        if r.status_code != 200:
            return {"error": r.read().decode("utf-8", errors="replace")[:200], "status": r.status_code}
        for evt, data in _sse(r):
            now = time.monotonic() - t0
            if watch:
                watch.sample()
            if evt == "status":
                marks.setdefault(f"status_{data.get('phase')}", round(now, 3))
            elif evt == "sources":
                marks.setdefault("sources", round(now, 3))
                marks["sources_n"] = len(data.get("sources", []))
            elif evt == "token":
                n += 1
                marks.setdefault("first_token", round(now, 3))
                text += data["text"]
                if stop_after and n >= stop_after and stop_t is None:
                    stop_t = time.monotonic()
                    with httpx.Client(base_url=c.base_url, headers=c.headers, timeout=10) as c2:
                        c2.post(f"/conversations/{cid}/stop")
            elif evt == "done":
                done = data["message"]
                marks["done"] = round(now, 3)
            elif evt == "error":
                marks["error"] = data
    out = {"marks": marks, "chars": len(text), "tokens_seen": n, "wall_s": round(time.monotonic() - t0, 3),
           "usage": (done or {}).get("usage"), "status": (done or {}).get("status"), "proc": watch.result() if watch else {}}
    if stop_t is not None:
        out["stop_to_done_s"] = round(time.monotonic() - stop_t, 3) if done else None
        # actual termination: llama-server cpu time flat for 0.5 s
        if psutil and pids:
            p = psutil.Process(pids[0])
            last = sum(p.cpu_times()[:2])
            t = time.monotonic()
            while time.monotonic() - t < 10:
                time.sleep(0.25)
                cur = sum(p.cpu_times()[:2])
                if cur - last < 0.02:
                    out["stop_to_cpu_idle_s"] = round(time.monotonic() - stop_t, 3)
                    break
                last = cur
    return out


def measure_app(model: Path, runtime: Path, reps: int, out_dir: Path, env_extra: dict | None, label: str) -> dict:
    app_root = out_dir / f"approot-{label}"
    if app_root.exists():
        shutil.rmtree(app_root)
    paths = PortablePaths.from_app_root(app_root)
    paths.ensure()
    db = Database(paths.db_path)
    db.open()
    reg = ModelRegistry(db, paths)
    # Install by hard copy (keeps the source untouched); role is detected from the header.
    # import_local refuses sources inside the data dir, so stage next to the app root
    staged = out_dir / "src" / model.name
    staged.parent.mkdir(parents=True, exist_ok=True)
    if not staged.exists() or staged.stat().st_size != model.stat().st_size:
        shutil.copy2(model, staged)
    installed = reg.import_local(staged)
    reg.set_state("active_chat_model_id", installed["id"])
    db.close()
    core = CoreProc(app_root, runtime, env_extra)
    result: dict = {"samples": []}
    result["core_start_s"] = core.start()
    with core.client() as c:
        t0 = time.monotonic()
        active = wait_ready(c)
        result["model_ready_s"] = round(time.monotonic() - t0, 2)
        result["runtime_status"] = active["runtime"]["chat"]
        pids = [core.proc.pid] + core.llama_pids()
        conv = c.post("/conversations", json={"title": "Messung"}).json()
        # cold: empty memory
        for kind, prompt in (("short", SHORT_PROMPT), ("long", LONG_PROMPT)):
            s = app_turn(c, conv["id"], prompt, pids)
            s.update({"mode": "app", "kind": kind, "cold": True, "memory": "empty"})
            result["samples"].append(s)
            result[f"ingest_drain_after_{kind}_s"] = wait_idle(c)
        # populate memory, then warm reps
        for f in WARMUP_FACTS:
            s = app_turn(c, conv["id"], f, pids)
            s.update({"mode": "app", "kind": "fact", "cold": False, "memory": "filling"})
            result["samples"].append(s)
            result.setdefault("ingest_drain_facts_s", []).append(wait_idle(c))
        conv2 = c.post("/conversations", json={"title": "Messung warm"}).json()
        for kind, prompt in (("short", SHORT_PROMPT), ("long", LONG_PROMPT), ("recall", "Wer leitet aktuell das Projekt Polarstern und wie hoch ist das Budget?")):
            for i in range(reps):
                s = app_turn(c, conv2["id"], prompt, pids)
                s.update({"mode": "app", "kind": kind, "cold": False, "memory": "populated", "rep": i})
                result["samples"].append(s)
                wait_idle(c)
        # stop latency
        s = app_turn(c, conv2["id"], "Schreibe eine lange Geschichte über eine Reise durch Europa, mindestens 600 Wörter.", pids, stop_after=5)
        s.update({"mode": "app", "kind": "stop", "cold": False, "memory": "populated"})
        result["samples"].append(s)
        result["ui_responsiveness_probe_ms"] = None  # measured in the UI, not here
    result["shutdown_s"] = core.shutdown()
    result["llama_after_shutdown"] = core.llama_pids()
    return result


def render_markdown(report: dict) -> str:
    lines = [f"# Messung {report['label']}", "", f"Modell: `{report['model']}`  ", f"Runtime: `{report['runtime']}` gpu_layers={report.get('gpu_layers')} threads={report.get('threads')}  ",
             f"Zeit: {report['started_at']}", ""]
    d = report.get("direct")
    if d:
        lines += [f"## Direkt (llama-server), Laden {d['load_s']} s", "", "| Art | kalt | n | TTFT median s | TTFT min/max | tok/s median | Prompt tok/s | CPU s/Antwort | Peak RSS MB |", "|---|---|---|---|---|---|---|---|---|"]
        for kind in ("short", "long"):
            for cold in (True, False):
                ss = [s for s in d["samples"] if s["kind"] == kind and s["cold"] == cold]
                if not ss:
                    continue
                t = summarize(ss, "ttft_s")
                tps = summarize(ss, "tps")
                ptps = summarize(ss, "prompt_tps")
                cpu = summarize([{"c": list(s["proc"].values())[0]["cpu_s"]} for s in ss if s["proc"]], "c")
                rss = max((list(s["proc"].values())[0]["peak_rss_mb"] for s in ss if s["proc"]), default=None)
                lines.append(f"| {kind} | {'ja' if cold else 'nein'} | {t['n']} | {t.get('median')} | {t.get('min')}/{t.get('max')} | {tps.get('median')} | {ptps.get('median')} | {cpu.get('median')} | {rss} |")
        lines.append("")
    a = report.get("app")
    if a:
        lines += [f"## App-Pfad, Kernstart {a['core_start_s']} s, Modell bereit nach {a['model_ready_s']} s", ""]
        lines += ["| Art | Gedächtnis | n | Suche fertig s | erstes Token s (median) | min/max | tok/s | Prompt-Tokens | Gesamt s | CPU s (Kern+Server) | Peak RSS MB (Server) |", "|---|---|---|---|---|---|---|---|---|---|---|"]
        for kind in ("short", "long", "recall", "fact", "stop"):
            for mem in ("empty", "filling", "populated"):
                ss = [s for s in a["samples"] if s.get("kind") == kind and s.get("memory") == mem and "marks" in s]
                if not ss:
                    continue
                ft = summarize([{"v": s["marks"].get("first_token")} for s in ss], "v")
                src = summarize([{"v": s["marks"].get("sources")} for s in ss], "v")
                tps = summarize([{"v": (s.get("usage") or {}).get("tokens_per_second")} for s in ss], "v")
                pt = summarize([{"v": (s.get("usage") or {}).get("prompt_tokens")} for s in ss], "v")
                wall = summarize(ss, "wall_s")
                cpu = summarize([{"v": sum((p.get("cpu_s") or 0) for p in s["proc"].values())} for s in ss], "v")
                rss = max((max((p["peak_rss_mb"] for p in s["proc"].values() if p["name"].startswith("llama")), default=0) for s in ss), default=None)
                lines.append(f"| {kind} | {mem} | {ft['n']} | {src.get('median')} | {ft.get('median')} | {ft.get('min')}/{ft.get('max')} | {tps.get('median')} | {pt.get('median')} | {wall.get('median')} | {cpu.get('median')} | {rss} |")
        stop = [s for s in a["samples"] if s.get("kind") == "stop"]
        if stop:
            s = stop[0]
            lines += ["", f"Stopp: POST /stop → done-Ereignis {s.get('stop_to_done_s')} s, llama-server CPU ruhig nach {s.get('stop_to_cpu_idle_s')} s, Status {s.get('status')}."]
        lines += ["", f"Shutdown {a['shutdown_s']} s; llama-server nach Shutdown: {a['llama_after_shutdown']}",
                  f"Ingest-Drain nach kalter Kurz-/Langantwort: {a.get('ingest_drain_after_short_s')} s / {a.get('ingest_drain_after_long_s')} s; nach den drei Fakten: {a.get('ingest_drain_facts_s')} s"]
    return "\n".join(lines) + "\n"


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", type=Path, required=True)
    ap.add_argument("--runtime", type=Path, default=HERE / "runtime" / "llama")
    ap.add_argument("--label", required=True)
    ap.add_argument("--out", type=Path, default=HERE / "measurements")
    ap.add_argument("--reps", type=int, default=3)
    ap.add_argument("--gpu-layers", type=int, default=None)
    ap.add_argument("--threads", type=int, default=None)
    ap.add_argument("--modes", default="direct,app")
    args = ap.parse_args()
    args.out.mkdir(parents=True, exist_ok=True)
    logs = args.out / "logs"
    logs.mkdir(exist_ok=True)
    report = {"label": args.label, "model": str(args.model), "runtime": str(args.runtime), "gpu_layers": args.gpu_layers,
              "threads": args.threads, "started_at": time.strftime("%Y-%m-%d %H:%M:%S"), "reps": args.reps,
              "host": {"cpu_count": os.cpu_count(), "ram_total_mb": round(psutil.virtual_memory().total / 2**20) if psutil else None}}
    modes = args.modes.split(",")
    if "direct" in modes:
        report["direct"] = asyncio.run(measure_direct(args.model, args.runtime, args.reps, args.gpu_layers, args.threads, logs))
        print("direct done", flush=True)
    if "app" in modes:
        env_extra = {}
        if args.gpu_layers is not None:
            env_extra["SPAIDER_FOLDABLE_GPU_LAYERS"] = str(args.gpu_layers)
        report["app"] = measure_app(args.model, args.runtime, args.reps, args.out, env_extra, args.label)
        print("app done", flush=True)
    (args.out / f"{args.label}.json").write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    md = render_markdown(report)
    (args.out / f"{args.label}.md").write_text(md, encoding="utf-8")
    print(md)
    return 0


if __name__ == "__main__":
    sys.exit(main())
