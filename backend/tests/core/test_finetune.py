# ruff: noqa: E501
"""Provider adapters run against reconstructed API fixtures; no real job has run."""
from __future__ import annotations

import asyncio
import json
from pathlib import Path

import httpx
import pytest

from app.config import settings
from app.services import purge_service
from app.services.finetune import gate
from app.services.finetune.adapters import OpenAIAdapter, ProviderNotConfigured, TinkerAdapter, TogetherAdapter
from app.services.finetune.gate import GateParams, GateRejected
from app.services.finetune.service import FineTuneService, artifact_root, serving_override
from app.services.graph_service import get_graph_service
from app.services.synthesizer import SynthesizeResult


@pytest.fixture(autouse=True)
def keys(monkeypatch, tmp_path):
    monkeypatch.setattr(settings, "finetune_openai_api_key", "k-openai")
    monkeypatch.setattr(settings, "finetune_together_api_key", "k-together")
    monkeypatch.setattr(settings, "finetune_tinker_api_key", "k-tinker")
    monkeypatch.setattr(settings, "data_dir", str(tmp_path))


class Provider:
    """A reconstructed OpenAI-style fine-tuning API."""

    def __init__(self, job_status="running", fail_on=None, model="ft:gpt-4o-mini:acme::abc"):
        self.job_status, self.fail_on, self.model, self.requests = job_status, fail_on, model, []

    def transport(self):
        return httpx.MockTransport(self.handle)

    def handle(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        path = request.url.path
        if self.fail_on and self.fail_on in path:
            return httpx.Response(400, json={"error": {"message": "bad file"}})
        if path.endswith("/files"):
            return httpx.Response(200, json={"id": "file-1"})
        if path.endswith("/fine_tuning/jobs"):
            return httpx.Response(200, json={"id": "job-1", "status": "queued"})
        if path.endswith("/cancel"):
            return httpx.Response(200, json={"id": "job-1", "status": "cancelled"})
        if "/fine_tuning/jobs/" in path:
            return httpx.Response(200, json={"id": "job-1", "status": self.job_status, "fine_tuned_model": self.model if self.job_status == "succeeded" else None, "error": {"message": "broke"} if self.job_status == "failed" else None})
        return httpx.Response(404)


async def _file(tmp_path) -> Path:
    path = tmp_path / "train.jsonl"
    path.write_text("{}\n", encoding="utf-8")
    return path


async def test_openai_adapter_speaks_its_api(tmp_path):
    provider = Provider(job_status="succeeded")
    adapter = OpenAIAdapter(transport=provider.transport())
    file_id = await adapter.upload(await _file(tmp_path))
    job_id = await adapter.create_job(file_id, "gpt-4o-mini", {"n_epochs": 2})
    job = await adapter.get_job(job_id)
    assert (file_id, job_id, job.status, job.model) == ("file-1", "job-1", "succeeded", provider.model)
    create = json.loads(provider.requests[1].content)
    assert create == {"training_file": "file-1", "model": "gpt-4o-mini", "hyperparameters": {"n_epochs": 2}}
    assert provider.requests[0].headers["authorization"] == "Bearer k-openai"
    assert b'name="purpose"' in provider.requests[0].content


async def test_together_adapter_speaks_its_api(tmp_path):
    seen = []

    def handle(request):
        seen.append(request)
        if request.url.path.endswith("/files/upload"):
            return httpx.Response(200, json={"id": "f"})
        if request.url.path.endswith("/fine-tunes"):
            return httpx.Response(200, json={"id": "ft-1"})
        return httpx.Response(200, json={"status": "completed", "output_name": "acme/model"})

    adapter = TogetherAdapter(transport=httpx.MockTransport(handle))
    assert await adapter.upload(await _file(tmp_path)) == "f"
    assert await adapter.create_job("f", "meta/llama", {"n_epochs": 1}) == "ft-1"
    job = await adapter.get_job("ft-1")
    assert job.status == "succeeded" and adapter.serving(job.model)["model"] == "together_ai/acme/model"


async def test_tinker_adapter_maps_states(tmp_path):
    def handle(request):
        if request.url.path.endswith("/datasets"):
            return httpx.Response(200, json={"id": "d"})
        if request.url.path.endswith("/training_runs"):
            return httpx.Response(200, json={"id": "r"})
        return httpx.Response(200, json={"status": "completed", "model_path": "tinker://m"})

    adapter = TinkerAdapter(transport=httpx.MockTransport(handle))
    assert await adapter.upload(await _file(tmp_path)) == "d"
    assert await adapter.create_job("d", "llama", {}) == "r"
    assert (await adapter.get_job("r")).model == "tinker://m"


def test_a_missing_key_is_reported(monkeypatch):
    monkeypatch.setattr(settings, "finetune_openai_api_key", "")
    with pytest.raises(ProviderNotConfigured):
        OpenAIAdapter()


@pytest.mark.parametrize(
    ("params", "message"),
    [
        (GateParams(confidence=0.5), "confidence"),
        (GateParams(min_delta=-0.1), "min_delta"),
        (GateParams(memory=False), "memory"),
        (GateParams(min_items=3), "min_items"),
    ],
)
def test_gate_floors_cannot_be_lowered(params, message):
    with pytest.raises(GateRejected, match=message):
        gate.validate(params)


def test_gate_passes_a_clear_improvement():
    result = gate.decide([0.2] * 30, [0.9] * 30, GateParams(), seed="s")
    assert result.passed and result.confidence == 1.0


def test_gate_rejects_no_improvement():
    assert not gate.decide([0.5] * 30, [0.5] * 30, GateParams(), seed="s").passed


def test_gate_rejects_a_worse_model():
    assert not gate.decide([0.9] * 30, [0.2] * 30, GateParams(), seed="s").passed


def test_gate_rejects_too_few_items():
    assert not gate.decide([0.2] * 5, [0.9] * 5, GateParams(), seed="s").passed


def test_gate_rejects_noise():
    base = [0.5] * 30
    candidate = [0.9 if index % 2 else 0.1 for index in range(30)]
    assert not gate.decide(base, candidate, GateParams(), seed="s").passed


def test_gate_is_deterministic():
    base = [0.4, 0.6] * 15
    candidate = [0.5, 0.65] * 15
    assert gate.decide(base, candidate, GateParams(), seed="s") == gate.decide(base, candidate, GateParams(), seed="s")


GOOD = {"passed": True, "items": 30, "confidence": 1.0, "mean_delta": 0.3}


@pytest.mark.parametrize(
    ("params", "result", "ok"),
    [
        (gate.as_dict(GateParams()), GOOD, True),
        (gate.as_dict(GateParams()), {**GOOD, "passed": False}, False),
        (gate.as_dict(GateParams()), {**GOOD, "items": 5}, False),
        (gate.as_dict(GateParams()), {**GOOD, "confidence": 0.5}, False),
        (gate.as_dict(GateParams()), {**GOOD, "mean_delta": 0.0}, False),
        ({**gate.as_dict(GateParams()), "confidence": 0.1}, GOOD, False),
        ({**gate.as_dict(GateParams()), "memory": False}, GOOD, False),
        ({**gate.as_dict(GateParams()), "min_items": 1}, GOOD, False),
        (None, GOOD, False),
        ({"unexpected": 1}, GOOD, False),
    ],
)
def test_stored_parameters_are_checked_again(params, result, ok):
    assert gate.recheck(params, result) is ok


def test_f1_score():
    assert gate.f1("Berlin", "Berlin") == 1.0
    assert gate.f1("", "Berlin") == 0.0
    assert 0 < gate.f1("the city of Berlin", "Berlin") < 1


# ── Lifecycle ─────────────────────────────────────────────────────────────


@pytest.fixture
def dataset(monkeypatch, tmp_path):
    async def synthesize(self, agent_id, config, max_clearance=5):
        captured["clearance"] = max_clearance
        path = tmp_path / "generated.jsonl"
        path.write_text('{"messages": []}\n', encoding="utf-8")
        return SynthesizeResult(agent_id=agent_id, output_path=str(path), total_examples=1, strategy_counts={}, duplicate_count=0)

    captured: dict = {}
    monkeypatch.setattr("app.services.finetune.service.ModelSynthesizer.synthesize", synthesize)
    return captured


def _service(provider):
    return FineTuneService(get_graph_service(), transport=provider.transport())


async def _principal(world, key):
    from app.core.authz import resolve_token

    return await resolve_token(key)


async def _create(world, kb, provider, **extra):
    owner = await _principal(world, world.owner_key)
    return await _service(provider).create_job(owner, kb["id"], provider="openai", base_model="gpt-4o-mini", **extra)


async def test_create_submits_and_tracks_the_job(auth_world, dataset):
    kb = await auth_world.create_kb()
    version = await _create(auth_world, kb, Provider())
    assert version["status"] == "training" and version["provider_job_id"] == "job-1"
    assert dataset["clearance"] == 1
    assert (artifact_root() / kb["id"] / version["id"] / "train.jsonl").exists()


async def test_a_rejected_upload_never_leaves_it_submitting(auth_world, dataset):
    from fastapi import HTTPException

    kb = await auth_world.create_kb()
    with pytest.raises(HTTPException) as caught:
        await _create(auth_world, kb, Provider(fail_on="/files"))
    assert caught.value.status_code == 502
    async with auth_world.driver.session() as session:
        rows = await (await session.run("MATCH (m:ModelVersion) RETURN m.status AS s, m.error AS e")).data()
    assert rows == [{"s": "failed", "e": "HTTPStatusError"}]


async def test_cancelling_during_submit_never_leaves_it_submitting(auth_world, monkeypatch, tmp_path):
    entered = asyncio.Event()

    async def hang(self, agent_id, config, max_clearance=5):
        entered.set()
        await asyncio.sleep(60)

    monkeypatch.setattr("app.services.finetune.service.ModelSynthesizer.synthesize", hang)
    kb = await auth_world.create_kb()
    task = asyncio.create_task(_create(auth_world, kb, Provider()))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    async with auth_world.driver.session() as session:
        rows = await (await session.run("MATCH (m:ModelVersion) RETURN m.status AS s")).data()
    assert rows == [{"s": "failed"}]


async def test_cannot_export_above_your_clearance(auth_world, dataset):
    from fastapi import HTTPException

    kb = await auth_world.create_kb()
    steward = await auth_world.member(kb["id"], "steward", clearance_level=1)
    with pytest.raises(HTTPException) as caught:
        await _service(Provider()).create_job(
            await _principal(auth_world, steward["api_key"]), kb["id"], provider="openai", base_model="m", data_clearance=3
        )
    assert caught.value.status_code == 403


async def _trained(world, kb, dataset_ready=True):
    provider = Provider(job_status="succeeded")
    owner = await _principal(world, world.owner_key)
    version = await _service(provider).create_job(owner, kb["id"], provider="openai", base_model="m")
    return await _service(provider).refresh(owner, version["id"]), provider, owner


ITEMS = [{"question": f"q{i}", "expected": "right answer"} for i in range(25)]


async def _answerer_better(question, override):
    return "right answer" if override else "wrong"


async def _answerer_same(question, override):
    return "wrong"


async def test_a_better_model_is_promoted_and_served(auth_world, dataset):
    kb = await auth_world.create_kb()
    version, provider, owner = await _trained(auth_world, kb)
    assert version["status"] == "trained"
    service = _service(provider)
    await service.evaluate(owner, version["id"], ITEMS, GateParams(), _answerer_better)
    promoted = await service.promote(owner, version["id"])
    assert promoted["status"] == "active"
    served = await serving_override(auth_world.driver, kb["id"], 5)
    assert served["model"] == provider.model and served["api_key"] == "k-openai"

    await service.rollback(owner, kb["id"])
    assert await serving_override(auth_world.driver, kb["id"], 5) is None


async def test_a_model_that_fails_the_gate_is_not_promoted(auth_world, dataset):
    from fastapi import HTTPException

    kb = await auth_world.create_kb()
    version, provider, owner = await _trained(auth_world, kb)
    service = _service(provider)
    await service.evaluate(owner, version["id"], ITEMS, GateParams(), _answerer_same)
    with pytest.raises(HTTPException) as caught:
        await service.promote(owner, version["id"])
    assert caught.value.status_code == 409


async def test_tampering_with_stored_params_blocks_promotion(auth_world, dataset):
    from fastapi import HTTPException

    kb = await auth_world.create_kb()
    version, provider, owner = await _trained(auth_world, kb)
    service = _service(provider)
    await service.evaluate(owner, version["id"], ITEMS, GateParams(), _answerer_better)
    async with auth_world.driver.session() as session:
        await session.run("MATCH (m:ModelVersion {id: $id}) SET m.eval_params = $p", id=version["id"], p=json.dumps({"confidence": 0.1, "min_delta": 0.0, "memory": True, "min_items": 20}))
    with pytest.raises(HTTPException):
        await service.promote(owner, version["id"])


async def test_promoting_needs_an_evaluation_first(auth_world, dataset):
    from fastapi import HTTPException

    kb = await auth_world.create_kb()
    version, provider, owner = await _trained(auth_world, kb)
    with pytest.raises(HTTPException) as caught:
        await _service(provider).promote(owner, version["id"])
    assert caught.value.status_code == 409


async def test_the_api_refuses_weak_gate_settings(auth_world, dataset):
    kb = await auth_world.create_kb()
    version, _, _ = await _trained(auth_world, kb)
    response = await auth_world.client.post(
        f"/api/v1/finetune/jobs/{version['id']}/evaluate",
        json={"items": ITEMS, "confidence": 0.1},
        headers=auth_world.headers(auth_world.owner_key),
    )
    assert response.status_code == 422


async def test_a_low_clearance_caller_gets_the_base_model(auth_world, dataset):
    kb = await auth_world.create_kb()
    version, provider, owner = await _trained(auth_world, kb)
    service = _service(provider)
    await service.evaluate(owner, version["id"], ITEMS, GateParams(), _answerer_better)
    await service.promote(owner, version["id"])
    async with auth_world.driver.session() as session:
        await session.run("MATCH (a:SystemAgent {agent_id: $kb}) SET a.answer_model_clearance = 3", kb=kb["id"])
    assert await serving_override(auth_world.driver, kb["id"], 2) is None
    assert await serving_override(auth_world.driver, kb["id"], 3) is not None


async def test_promoting_a_second_model_retires_the_first(auth_world, dataset):
    kb = await auth_world.create_kb()
    first, provider, owner = await _trained(auth_world, kb)
    second, _, _ = await _trained(auth_world, kb)
    service = _service(provider)
    for version in (first, second):
        await service.evaluate(owner, version["id"], ITEMS, GateParams(), _answerer_better)
        await service.promote(owner, version["id"])
    statuses = {row["id"]: row["status"] for row in await service.list_for(owner, kb["id"])}
    assert statuses == {first["id"]: "retired", second["id"]: "active"}


async def test_deleting_a_kb_removes_fine_tune_artifacts(auth_world, dataset, monkeypatch):
    monkeypatch.setattr(purge_service, "SETTLE_SECONDS", 0)
    kb = await auth_world.create_kb()
    await _create(auth_world, kb, Provider())
    assert (artifact_root() / kb["id"]).exists()
    response = await auth_world.client.delete(f"/api/v1/agents/{kb['id']}", headers=auth_world.headers(auth_world.owner_key))
    assert response.status_code == 200
    assert not (artifact_root() / kb["id"]).exists()
    async with auth_world.driver.session() as session:
        assert await (await session.run("MATCH (m:ModelVersion) RETURN m")).data() == []


async def test_routes_need_the_right_role(auth_world, dataset):
    kb = await auth_world.create_kb()
    reader = await auth_world.member(kb["id"], "reader")
    response = await auth_world.client.post(
        "/api/v1/finetune/jobs",
        json={"agent_id": kb["id"], "provider": "openai", "base_model": "m"},
        headers=auth_world.headers(reader["api_key"]),
    )
    assert response.status_code == 403


async def test_unknown_provider_is_a_client_error(auth_world, dataset):
    kb = await auth_world.create_kb()
    response = await auth_world.client.post(
        "/api/v1/finetune/jobs",
        json={"agent_id": kb["id"], "provider": "nope", "base_model": "m"},
        headers=auth_world.headers(auth_world.owner_key),
    )
    assert response.status_code == 422
