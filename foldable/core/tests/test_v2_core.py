"""v0.2 core tests: scheduler preemption, activity events, immediate memory
visibility, runtime option planning, capabilities, recommendations with
simulated hardware, and remote GGUF header parsing (local range server)."""

from __future__ import annotations

import asyncio
import http.server
import struct
import threading

import pytest

from spaider_foldable.activity import ActivityBus, ActivityTracker
from spaider_foldable.hardware import GpuInfo, HardwareProfile
from spaider_foldable.inference.options import estimate_kv_bytes, plan
from spaider_foldable.memory.ingest import MemoryIngestor
from spaider_foldable.memory.retrieve import MemoryRetriever
from spaider_foldable.memory.store import LocalMemoryStore, Origin
from spaider_foldable.models import hub
from spaider_foldable.models.capabilities import capabilities_for_model, classify_type, detect_family
from spaider_foldable.models.catalog import load_catalog
from spaider_foldable.models.recommend import hard_filter, recommend
from spaider_foldable.scheduler import LLMScheduler, Preempted

from .gguf_fixture import write_fake_gguf
from .test_memory import _NoEmbeddings

GiB = 1024**3


def _hw(ram_gb: float, *, gpu_free_gb: float | None = None, disk_gb: float = 200, fs: str = "NTFS",
        avail_gb: float | None = None, variants=("cpu", "vulkan")) -> HardwareProfile:  # fmt: skip
    gpus = []
    if gpu_free_gb is not None:
        gpus.append(GpuInfo(name="Test GPU", backend="vulkan", device="Vulkan0", vram_total_bytes=int(gpu_free_gb * GiB) + GiB,  # noqa: E501
                            vram_free_bytes=int(gpu_free_gb * GiB)))  # fmt: skip
    return HardwareProfile(
        cpu={"name": "Test CPU", "cores_physical": 8, "threads": 16, "features": []}, ram_total_bytes=int(ram_gb * GiB),
        ram_available_bytes=int((avail_gb if avail_gb is not None else ram_gb * 0.6) * GiB), gpus=gpus,
        disk_free_bytes=int(disk_gb * GiB), disk_total_bytes=int(disk_gb * 2 * GiB), filesystem=fs,
        max_file_bytes=4_294_967_295 if fs == "FAT32" else None, power={}, fingerprint="test", runtime_variants=list(variants),  # noqa: E501
    )  # fmt: skip


# ---------------------------------------------------------------- scheduler
@pytest.mark.asyncio
async def test_scheduler_chat_preempts_background():
    sched = LLMScheduler(cooldown_s=0.0)
    started = asyncio.Event()
    cancelled = asyncio.Event()

    async def slow_bg():
        started.set()
        try:
            await asyncio.sleep(10)
        except asyncio.CancelledError:
            cancelled.set()
            raise

    bg = asyncio.create_task(sched.run_background("message", slow_bg()))
    await started.wait()
    assert sched.state()["background"]["kind"] == "message"
    async with sched.chat():
        assert sched.state()["chat_active"]
        assert cancelled.is_set()
    with pytest.raises(Preempted):
        await bg
    assert sched.preemptions == 1
    assert sched.state()["background"]["kind"] is None


@pytest.mark.asyncio
async def test_scheduler_background_waits_for_cooldown_and_pause():
    sched = LLMScheduler(cooldown_s=0.3)
    async with sched.chat():
        pass
    t0 = asyncio.get_running_loop().time()
    await sched.run_background("chunk", asyncio.sleep(0))
    assert asyncio.get_running_loop().time() - t0 >= 0.25  # waited for the cooldown
    sched.pause()
    task = asyncio.create_task(sched.run_background("chunk", asyncio.sleep(0)))
    await asyncio.sleep(0.4)
    assert not task.done()  # paused scheduler never grants the slot
    sched.resume()
    await asyncio.wait_for(task, timeout=2)


# ---------------------------------------------------------------- activity
def test_activity_tracker_sequence_and_timings():
    bus = ActivityBus()
    seen = []
    tr = ActivityTracker("req-1", bus, conversation_id="c1", emit=seen.append)
    tr.start("memory_search", "suche")
    tr.done("memory_search", "3 Quellen", {"sources": 3}, source_ids=["a", "b", "c"])
    tr.start("generating", "schreibt")
    assert tr.progress("generating", "p1", {"tokens": 1}) is not None
    assert tr.progress("generating", "p2", {"tokens": 2}) is None  # rate limited
    tr.cancel("generating", "Abgebrochen")
    seqs = [e.seq for e in seen]
    assert seqs == sorted(seqs) and seqs[0] == 1
    assert [e.phase for e in seen] == ["memory_search", "memory_search", "generating", "generating", "generating"]
    assert seen[1].state == "done" and seen[1].source_ids == ["a", "b", "c"] and seen[1].counters["sources"] == 3
    assert seen[-1].state == "cancelled"
    assert "memory_search_ms" in tr.timings_ms and "generating_ms" in tr.timings_ms
    hist = bus.history("req-1")
    assert len(hist) == 5 and hist[0]["conversation_id"] == "c1"
    assert bus.history("unknown") == []


# ---------------------------------------------------------------- immediate memory visibility
@pytest.mark.asyncio
async def test_raw_fact_is_retrievable_before_extraction_and_enriched_after(db, monkeypatch):
    store = LocalMemoryStore(db)
    retriever = MemoryRetriever(db, store, _NoEmbeddings())
    fid = "fact-raw-1"
    store.write_raw_fact(fid, "Korrektur: Der Projektleiter von Orion ist jetzt Priya Nair.", Origin("conversation", "c1", "A"),  # noqa: E501
                         source="conversation:c1")  # fmt: skip
    ctx = await retriever.retrieve("Wer leitet Orion?")
    assert "Priya Nair" in ctx.context_text and "Nachverarbeitung ausstehend" in ctx.context_text
    assert any(s["id"] == fid and s["processing"] == "pending" for s in ctx.sources)
    # the message being answered is excluded from its own context
    ctx2 = await retriever.retrieve("Wer leitet Orion?", exclude_fact_ids={fid})
    assert "Priya Nair" not in ctx2.context_text
    # background extraction enriches the same node instead of duplicating it
    ing = MemoryIngestor(db, store, _NoEmbeddings())

    async def fake_extract(text, context=None):
        from app.models.schemas import Edge, GraphPayload, Node

        a = Node(label="Priya Nair", type="PERSON", properties={"source_text": text})
        b = Node(label="Orion", type="PROJECT", properties={"source_text": text})
        return GraphPayload(
            nodes=[a, b],
            edges=[Edge(source_id=a.id, target_id=b.id, relation="LEADS", properties={"source_text": text})],
        )

    monkeypatch.setattr(ing.compressor, "extract", fake_extract)
    ing.supersession_enabled = False
    res = await ing.ingest_text("Korrektur: Der Projektleiter von Orion ist jetzt Priya Nair.", Origin("conversation", "c1", "A"),  # noqa: E501
                                fact_id=fid)  # fmt: skip
    assert res["fact_id"] == fid
    assert db.scalar("SELECT count(*) FROM nodes WHERE type = 'FACT'") == 1
    assert db.scalar("SELECT processing FROM nodes WHERE id = ?", (fid,)) == "extracted"
    assert db.scalar("SELECT count(*) FROM edges WHERE source_id = ? AND relation = 'MENTIONS'", (fid,)) == 2
    ctx3 = await retriever.retrieve("Wer leitet Orion?")
    assert "Nachverarbeitung ausstehend" not in ctx3.context_text and "Priya Nair" in ctx3.context_text


@pytest.mark.asyncio
async def test_ingest_worker_waits_while_chat_model_loads(db, monkeypatch):
    """Items must not be degraded to a no-LLM ingest while the configured model is still loading."""
    from spaider_foldable.memory.ingest import IngestQueue, IngestWorker
    from spaider_foldable.store.conversations import ConversationStore

    convs = ConversationStore(db)
    conv = convs.create("Warten")
    msg = convs.add_message(conv["id"], "user", "Merk dir: Der Projektleiter von Orion ist Priya Nair.")
    queue = IngestQueue(db)
    queue.enqueue("message", msg["id"], fact_id="fact-wait-1")
    calls: list[bool] = []

    class _Ingestor:
        async def ingest_text(self, text, origin, *, source, llm_available=True, fact_id=None):
            calls.append(llm_available)
            return {"fact_id": fact_id}

    state = {"chat": "starting"}
    worker = IngestWorker(
        queue,
        _Ingestor(),
        db,
        is_llm_ready=lambda: state["chat"] == "ready",
        llm_state=lambda: state["chat"],
        scheduler=LLMScheduler(cooldown_s=0.0),
    )
    worker.start()
    await asyncio.sleep(1.5)
    assert calls == [] and queue.pending_count() == 1  # still waiting for the runtime
    state["chat"] = "ready"
    for _ in range(40):
        await asyncio.sleep(0.1)
        if calls:
            break
    await worker.stop()
    assert calls == [True] and queue.pending_count() == 0
    # without any model (stopped/error) the worker proceeds without the LLM, as before
    queue.enqueue("message", msg["id"], fact_id="fact-wait-2")
    state["chat"] = "stopped"
    worker2 = IngestWorker(
        queue,
        _Ingestor(),
        db,
        is_llm_ready=lambda: False,
        llm_state=lambda: state["chat"],
        scheduler=LLMScheduler(cooldown_s=0.0),
    )
    worker2.start()
    for _ in range(40):
        await asyncio.sleep(0.1)
        if len(calls) == 2:
            break
    await worker2.stop()
    assert calls == [True, False]


# ---------------------------------------------------------------- deterministic corrections
def test_question_and_correction_detection():
    from spaider_foldable.memory.corrections import correction_strength, find_corrected_facts, is_question_only

    assert is_question_only("Wer leitet das Projekt Orion?")
    assert is_question_only("Wie hieß der Hund? Und die Katze?")
    assert not is_question_only("Kannst du dir merken, dass mein Hund Rex heißt?")
    assert not is_question_only("Mein Hund heißt Rex.")
    assert correction_strength("Korrektur: Der Leiter von Orion ist jetzt Max Berg.") == "strong"
    assert correction_strength("Priya Nair leitet jetzt auch das Projekt Atlas.") == "weak"
    assert correction_strength("Das Budget beträgt 2 Millionen.") is None
    assert correction_strength("Wer leitet Orion jetzt?") is None
    old = [
        {"id": "f-lead", "description": "Der Projektleiter von Orion ist Max Berg."},
        {"id": "f-budget", "description": "Das Budget von Orion beträgt 2 Millionen Euro."},
        {"id": "f-q", "description": "Wer ist der Projektleiter von Orion?"},
    ]
    # strong marker, half of the old content words shared -> replaced
    assert find_corrected_facts("Korrektur: Der Projektleiter von Orion ist jetzt Priya Nair.", old) == ["f-lead"]
    # weak marker with only half overlap -> not replaced (second role, not a correction)
    assert find_corrected_facts("Max Berg leitet jetzt auch das Projekt Atlas.", old) == []
    # weak marker with high overlap -> replaced
    assert find_corrected_facts("Das Budget von Orion beträgt inzwischen 3 Millionen Euro.", old) == ["f-budget"]
    # repeating the same statement is not a correction
    assert find_corrected_facts("Korrektur: Der Projektleiter von Orion ist Max Berg.", old) == []


@pytest.mark.asyncio
async def test_explicit_correction_is_visible_before_extraction(db):
    store = LocalMemoryStore(db)
    retriever = MemoryRetriever(db, store, _NoEmbeddings())
    origin = Origin("conversation", "c1", "A")
    store.write_raw_fact("f1", "Merk dir: Der Projektleiter von Orion ist Max Berg.", origin, source="conversation:c1")
    assert store.apply_explicit_correction("f1", "Merk dir: Der Projektleiter von Orion ist Max Berg.") == []
    store.write_raw_fact(
        "f2", "Korrektur: Der Projektleiter von Orion ist jetzt Priya Nair.", origin, source="conversation:c1"
    )
    assert store.apply_explicit_correction("f2", "Korrektur: Der Projektleiter von Orion ist jetzt Priya Nair.") == ["f1"]
    assert db.scalar("SELECT superseded_by FROM nodes WHERE id = 'f1'") == "f2"
    ctx = await retriever.retrieve("Wer leitet Orion?")
    current = [s for s in ctx.sources if s.get("status") != "superseded"]
    assert any("Priya Nair" in s["excerpt"] for s in current)
    assert not any("Max Berg" in s["excerpt"] and "Priya" not in s["excerpt"] for s in current)
    # idempotent
    assert store.apply_explicit_correction("f2", "Korrektur: Der Projektleiter von Orion ist jetzt Priya Nair.") == []
    # an unrelated statement with a weak marker does not touch the current fact
    store.write_raw_fact("f3", "Priya Nair leitet jetzt auch das Projekt Atlas.", origin, source="conversation:c1")
    assert store.apply_explicit_correction("f3", "Priya Nair leitet jetzt auch das Projekt Atlas.") == []
    assert db.scalar("SELECT superseded FROM nodes WHERE id = 'f2'") == 0


# ---------------------------------------------------------------- runtime options
def test_plan_modes_and_gpu_decisions():
    hw = _hw(32, gpu_free_gb=12)
    kv = estimate_kv_bytes(8192, n_layers=36, n_kv_heads=8, head_dim=128)
    assert kv == 2 * 36 * 8192 * 8 * 128 * 2
    auto = plan("auto", "auto", hw, weights_bytes=2 * GiB, kv_bytes=kv)
    assert auto.backend == "vulkan" and auto.gpu_layers == 999 and auto.threads == 8 and auto.threads_batch == 16
    quiet = plan("quiet", "auto", hw, weights_bytes=2 * GiB, kv_bytes=kv)
    assert quiet.threads == 4 and quiet.prio == -1
    off = plan("auto", "off", hw, weights_bytes=2 * GiB, kv_bytes=kv)
    assert off.backend == "cpu"
    failed = plan("auto", "auto", hw, weights_bytes=2 * GiB, kv_bytes=kv, gpu_failed_before=True)
    assert failed.backend == "cpu" and "fehlgeschlagen" in failed.reason_de
    partial = plan("fast", "auto", hw, weights_bytes=16 * GiB, kv_bytes=kv)
    assert (
        partial.backend == "vulkan" and partial.gpu_layers is not None and partial.gpu_layers < 0
    )  # percentage marker
    too_big = plan("auto", "auto", hw, weights_bytes=60 * GiB, kv_bytes=kv)
    assert too_big.backend == "cpu"
    nogpu = plan("auto", "auto", _hw(16), weights_bytes=2 * GiB, kv_bytes=kv)
    assert nogpu.backend == "cpu"
    assert estimate_kv_bytes(4096, n_layers=None, n_kv_heads=None, head_dim=None) == 4096 * 128 * 1024


# ---------------------------------------------------------------- capabilities
def test_capabilities_and_type_classification():
    assert detect_family("qwen3", "Qwen3 4B")["id"] == "Qwen3"
    assert detect_family("qwen35", "Qwen3.8 27B")["id"] == "Qwen3.5"
    assert detect_family("llama", "Llama 3.2 1B Instruct")["id"] == "Llama"
    assert detect_family("gemma3", "gemma-3-1b-it")["system_role"] is False
    m = {"architecture": "qwen3", "name": "Qwen3 4B", "context_length": 32768, "role": "chat",
         "metadata": {"has_chat_template": True, "chat_template_excerpt": "{% if enable_thinking %}..."}}  # fmt: skip
    caps = capabilities_for_model(m, {"cpu": None, "vulkan": None})
    assert (
        caps["reasoning_toggle"] == "enable_thinking"
        and caps["backends"] == ["cpu", "vulkan"]
        and caps["type"] == "chat"
    )
    m2 = dict(m, metadata={"has_chat_template": True, "chat_template_excerpt": "{{ messages }}"})
    assert capabilities_for_model(m2)["reasoning_toggle"] is None  # template without the switch
    assert classify_type({"architecture": "qwen3", "name": "x", "metadata": {"qwen3.pooling_type": 3}}) == "embedding"
    assert classify_type({"architecture": "llama", "name": "x", "metadata": {"has_chat_template": False}}) == "base"
    assert classify_type({"architecture": "clip", "name": "mmproj", "metadata": {}}) == "vision"
    assert classify_type({"architecture": "whisper", "name": "x", "metadata": {}}) == "audio"


# ---------------------------------------------------------------- recommendations
def test_recommendations_filter_and_rank_with_simulated_profiles():
    cat = load_catalog()
    entries = [e for e in cat.entries if e.source_kind == "builtin"]

    def none(_e):
        return None

    # 32 GB, 12 GB GPU: balanced / fast / quality present, 27B excluded (RAM), embeddings excluded (type)
    r = recommend(entries, _hw(32, gpu_free_gb=12), focus="alltag", context_tokens=8192, installed_ids={}, measured_for=none,  # noqa: E501
                  embedding_enabled=True)  # fmt: skip
    slots = [p["slot"] for p in r["picks"]]
    assert slots[0] == "balanced" and "fast" in slots and len(r["picks"]) <= 3
    assert all(p["entry"].type == "chat" for p in r["picks"])
    r16 = recommend(
        entries,
        _hw(16),
        focus="alltag",
        context_tokens=8192,
        installed_ids={},
        measured_for=none,
        embedding_enabled=False,
    )
    assert any(x["id"].startswith("qwen3.8-27b") for x in r16["excluded"])
    assert all("Geschwindigkeit hier noch nicht gemessen." in p["reasons_de"] for p in r["picks"])
    # 8 GB laptop without GPU: only small models survive the hard filter
    r8 = recommend(
        entries,
        _hw(8),
        focus="alltag",
        context_tokens=8192,
        installed_ids={},
        measured_for=none,
        embedding_enabled=False,
    )
    for p in r8["picks"]:
        assert p["estimate"]["ram_bytes"] <= 8 * GiB - 3 * GiB
    assert any("Qwen3 8B" in x["name"] for x in r8["excluded"])
    # weights fit but context reserve does not (4 GB): nothing or only tiny models
    r4 = recommend(
        entries,
        _hw(4),
        focus="alltag",
        context_tokens=32768,
        installed_ids={},
        measured_for=none,
        embedding_enabled=False,
    )
    assert len(r4["picks"]) <= 1
    # FAT32 excludes files > 4 GB, small disk excludes downloads
    e8 = cat.get("qwen3-8b-q6_k")
    est, reason, _ = hard_filter(e8, _hw(64, fs="FAT32"), context_tokens=8192, installed=False, embedding_enabled=False)
    assert est is None and "FAT32" in reason
    est, reason, _ = hard_filter(cat.get("qwen3-4b-q4_k_m"), _hw(64, disk_gb=1), context_tokens=8192, installed=False,
                                 embedding_enabled=False)  # fmt: skip
    assert est is None and "freier Speicher" in reason
    # gated / unverified entries never become recommendations
    gated = cat.get("qwen3-1.7b-q8_0").model_copy(deep=True)
    gated.license.gated = True
    est, reason, _ = hard_filter(gated, _hw(32), context_tokens=8192, installed=False, embedding_enabled=False)
    assert est is None and "Konto" in reason
    unv = cat.get("qwen3-1.7b-q8_0").model_copy(deep=True)
    unv.verification.status = "unverified"
    assert hard_filter(unv, _hw(32), context_tokens=8192, installed=False, embedding_enabled=False)[0] is None
    # measured data is shown only when present and never invented
    measured = {"tokens_per_second": 31.0, "backend": "cpu"}
    rm = recommend(entries, _hw(32), focus="alltag", context_tokens=8192, installed_ids={},
                   measured_for=lambda e: measured if e.id == "qwen3-4b-q4_k_m" else None, embedding_enabled=False)  # fmt: skip  # noqa: E501
    pick = next(p for p in rm["picks"] if p["entry"].id == "qwen3-4b-q4_k_m")
    assert pick["measured"] == measured and any("31 Token/s" in s for s in pick["reasons_de"])


# ---------------------------------------------------------------- remote GGUF header
class _RangeHandler(http.server.SimpleHTTPRequestHandler):
    payload = b""

    def log_message(self, *a):
        pass

    def do_GET(self):
        rng = self.headers.get("Range")
        data = self.payload
        if rng:
            start, end = rng.split("=")[1].split("-")
            start = int(start)
            end = min(int(end), len(data) - 1) if end else len(data) - 1
            body = data[start : end + 1]
            self.send_response(206)
            self.send_header("Content-Range", f"bytes {start}-{end}/{len(data)}")
        else:
            body = data
            self.send_response(200)
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)


def test_remote_gguf_header_and_split_detection(tmp_path, monkeypatch):
    gguf = write_fake_gguf(
        tmp_path / "remote.gguf", architecture="qwen3", name="Remote Model", file_type=15, extra_bytes=5_000_000
    )
    _RangeHandler.payload = gguf.read_bytes()
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", 0), _RangeHandler)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    try:
        port = srv.server_address[1]
        monkeypatch.setattr(hub, "_host_allowed", lambda url: True)
        monkeypatch.setattr(hub, "HEADER_FIRST_BYTES", 64)  # force the grow-and-retry path
        orig = hub._client

        class _C:
            def __init__(self, timeout):
                self._c = orig(timeout)

            def __enter__(self):
                return self

            def __exit__(self, *a):
                self._c.close()

            def get(self, url, **kw):
                return self._c.get(f"http://127.0.0.1:{port}/remote.gguf", **kw)

        monkeypatch.setattr(hub, "_client", _C)
        header = hub.remote_gguf_header("x/y", "main", "remote.gguf")
        assert header["architecture"] == "qwen3" and header["quantization"] == "Q4_K_M" and header["has_chat_template"]
        assert header["architecture_supported"] is True and header["context_length"] == 4096
    finally:
        srv.shutdown()
    files = [
        hub.HubFile(path="m-00001-of-00003.gguf", size=1, sha256="a", split_index=1, split_total=3, split_base="m"),
        hub.HubFile(path="m-00002-of-00003.gguf", size=1, sha256="b", split_index=2, split_total=3, split_base="m"),
    ]
    with pytest.raises(hub.HubError) as exc:
        hub.split_group(files, files[0])
    assert exc.value.code == "split_incomplete"
    single = hub.HubFile(path="s-Q4_K_M.gguf", size=1, sha256="c")
    assert hub.split_group([single], single) == [single]
    assert (
        hub.pick_default_file([single, hub.HubFile(path="mmproj-x.gguf", size=1, sha256=None, quantization="F16")])
        is single
    )
    with pytest.raises(hub.HubError):
        hub.repo_info("not a repo id")


def test_fake_gguf_struct_helper():
    # sanity for the fixture used above: header must parse as a uint32 version 3
    data = write_fake_gguf(__import__("pathlib").Path(__import__("tempfile").mkdtemp()) / "t.gguf").read_bytes()
    assert data[:4] == b"GGUF" and struct.unpack("<I", data[4:8])[0] == 3
