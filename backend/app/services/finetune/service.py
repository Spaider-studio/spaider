"""The model registry: from a KB's data to a promoted, serving model."""
from __future__ import annotations

import asyncio
import json
import logging
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Awaitable, Callable, Optional

from fastapi import HTTPException, status

from app.config import settings
from app.core.authz import Action, Principal, authorize
from app.core.kv import bump_cache_generation
from app.services.finetune import gate
from app.services.finetune.adapters import ProviderNotConfigured, get_adapter
from app.services.synthesizer import ModelSynthesizer, SynthesizeConfig

logger = logging.getLogger(__name__)

_NOT_FOUND = "Model version not found."
Answerer = Callable[[str, Optional[dict]], Awaitable[str]]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def artifact_root() -> Path:
    return Path(settings.data_dir) / "finetune"


def purge_artifacts(agent_id: str) -> None:
    if Path(agent_id).name != agent_id or agent_id in {"", ".", ".."}:
        raise ValueError("Invalid KB id.")
    shutil.rmtree(artifact_root() / agent_id, ignore_errors=True)


class FineTuneService:
    def __init__(self, graph, *, transport=None) -> None:
        self._graph = graph
        self._driver = graph._driver
        self._transport = transport

    async def _set(self, version_id: str, **fields) -> dict:
        async with self._driver.session() as session:
            record = await (
                await session.run(
                    "MATCH (m:ModelVersion {id: $id}) SET m += $fields RETURN properties(m) AS m",
                    id=version_id,
                    fields={**fields, "updated_at": _now()},
                )
            ).single()
        return dict(record["m"])

    async def get(self, principal: Principal, version_id: str, action: Action = Action.READ) -> dict:
        async with self._driver.session() as session:
            record = await (
                await session.run("MATCH (m:ModelVersion {id: $id}) RETURN properties(m) AS m", id=version_id)
            ).single()
        if record is None:
            raise HTTPException(status.HTTP_404_NOT_FOUND, detail=_NOT_FOUND)
        version = dict(record["m"])
        await authorize(principal, version["agent_id"], action)
        return version

    async def list_for(self, principal: Principal, agent_id: str) -> list[dict]:
        await authorize(principal, agent_id, Action.READ)
        async with self._driver.session() as session:
            result = await session.run(
                "MATCH (m:ModelVersion {agent_id: $kb}) RETURN properties(m) AS m ORDER BY m.created_at DESC LIMIT 100",
                kb=agent_id,
            )
            return [dict(record["m"]) async for record in result]

    def _adapter(self, provider: str):
        try:
            return get_adapter(provider, self._transport)
        except KeyError:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"Unknown provider '{provider}'.")
        except ProviderNotConfigured:
            raise HTTPException(status.HTTP_503_SERVICE_UNAVAILABLE, detail=f"No API key is set for '{provider}'.")

    async def create_job(
        self,
        principal: Principal,
        agent_id: str,
        *,
        provider: str,
        base_model: str,
        data_clearance: int = 1,
        max_examples: int = 500,
        hyperparameters: Optional[dict] = None,
    ) -> dict:
        """Prepare data and submit it. Training data leaves this system for the provider.

        ``data_clearance`` caps what is exported, and a caller can never export
        above their own clearance.
        """
        await authorize(principal, agent_id, Action.MANAGE)
        if data_clearance > principal.clearance_level:
            raise HTTPException(status.HTTP_403_FORBIDDEN, detail="Cannot export above your own clearance.")
        adapter = self._adapter(provider)
        version_id = str(uuid.uuid4())
        async with self._driver.session() as session:
            await session.run(
                "CREATE (m:ModelVersion) SET m = $version",
                version={
                    "id": version_id,
                    "agent_id": agent_id,
                    "provider": provider,
                    "base_model": base_model,
                    "status": "submitting",
                    "data_clearance": data_clearance,
                    "created_by": principal.id,
                    "created_at": _now(),
                },
            )
        try:
            dataset = await self._prepare_dataset(agent_id, version_id, data_clearance, max_examples)
            file_id = await adapter.upload(dataset)
            job_id = await adapter.create_job(file_id, base_model, hyperparameters or {})
        except BaseException as exc:
            # Whatever went wrong, including cancellation, the version must not
            # stay in "submitting" for ever.
            await asyncio.shield(self._set(version_id, status="failed", error=type(exc).__name__))
            if isinstance(exc, Exception):
                logger.warning("Fine-tune submission failed for %s: %s", agent_id, type(exc).__name__)
                raise HTTPException(status.HTTP_502_BAD_GATEWAY, detail="The provider rejected the job.") from exc
            raise
        return await self._set(version_id, status="training", provider_job_id=job_id, provider_file_id=file_id)

    async def _prepare_dataset(self, agent_id: str, version_id: str, clearance: int, max_examples: int) -> Path:
        result = await ModelSynthesizer(self._graph).synthesize(
            agent_id,
            SynthesizeConfig(output_format="openai", max_examples=max_examples),
            max_clearance=clearance,
        )
        if result.total_examples == 0:
            raise ValueError("No training examples could be produced.")
        target = artifact_root() / agent_id / version_id / "train.jsonl"

        def _move() -> None:
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(result.output_path, target)

        await asyncio.to_thread(_move)
        return target

    async def refresh(self, principal: Principal, version_id: str) -> dict:
        version = await self.get(principal, version_id)
        if version["status"] != "training":
            return version
        job = await self._adapter(version["provider"]).get_job(version["provider_job_id"])
        if job.status == "succeeded":
            return await self._set(version_id, status="trained", fine_tuned_model=job.model)
        if job.status in ("failed", "cancelled"):
            return await self._set(version_id, status=job.status, error=(job.error or "")[:500])
        return version

    async def cancel(self, principal: Principal, version_id: str) -> dict:
        version = await self.get(principal, version_id, Action.MANAGE)
        if version["status"] != "training":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="Only a training job can be cancelled.")
        await self._adapter(version["provider"]).cancel(version["provider_job_id"])
        return await self._set(version_id, status="cancelled")

    async def evaluate(
        self,
        principal: Principal,
        version_id: str,
        items: list[dict],
        params: gate.GateParams,
        answer: Answerer,
    ) -> dict:
        """Score base and candidate on the same questions, then apply the gate."""
        try:
            gate.validate(params)
        except gate.GateRejected as exc:
            raise HTTPException(status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc))
        version = await self.get(principal, version_id, Action.MANAGE)
        if version["status"] not in ("trained", "evaluated"):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="The model has not finished training.")
        candidate = self._adapter(version["provider"]).serving(version["fine_tuned_model"])
        base_scores, candidate_scores = [], []
        for item in items:
            base_scores.append(gate.f1(await answer(item["question"], None), item["expected"]))
            candidate_scores.append(gate.f1(await answer(item["question"], candidate), item["expected"]))
        result = gate.decide(base_scores, candidate_scores, params, seed=version_id)
        return await self._set(
            version_id,
            status="evaluated",
            eval_params=json.dumps(gate.as_dict(params)),
            eval_result=json.dumps(gate.as_dict(result)),
        )

    async def promote(self, principal: Principal, version_id: str) -> dict:
        version = await self.get(principal, version_id, Action.MANAGE)
        if version["status"] != "evaluated":
            raise HTTPException(status.HTTP_409_CONFLICT, detail="The model has not been evaluated.")
        params = json.loads(version.get("eval_params") or "null")
        result = json.loads(version.get("eval_result") or "null")
        if not gate.recheck(params, result):
            raise HTTPException(status.HTTP_409_CONFLICT, detail="The model did not pass the evaluation gate.")
        await self._retire_active(version["agent_id"], except_id=version_id)
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (a:SystemAgent {agent_id: $kb})
                SET a.answer_model = $model, a.answer_provider = $provider, a.answer_model_clearance = $clearance
                """,
                kb=version["agent_id"],
                model=version["fine_tuned_model"],
                provider=version["provider"],
                clearance=version["data_clearance"],
            )
        await bump_cache_generation(version["agent_id"])
        return await self._set(version_id, status="active", promoted_by=principal.id, promoted_at=_now())

    async def _retire_active(self, agent_id: str, except_id: Optional[str] = None) -> None:
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (m:ModelVersion {agent_id: $kb, status: 'active'})
                WHERE $keep IS NULL OR m.id <> $keep
                SET m.status = 'retired', m.updated_at = $now
                """,
                kb=agent_id,
                keep=except_id,
                now=_now(),
            )

    async def rollback(self, principal: Principal, agent_id: str) -> dict:
        """Go back to the base model."""
        await authorize(principal, agent_id, Action.MANAGE)
        await self._retire_active(agent_id)
        async with self._driver.session() as session:
            await session.run(
                """
                MATCH (a:SystemAgent {agent_id: $kb})
                REMOVE a.answer_model, a.answer_provider, a.answer_model_clearance
                """,
                kb=agent_id,
            )
        await bump_cache_generation(agent_id)
        return {"agent_id": agent_id, "answer_model": None}


async def serving_override(driver, agent_id: str, clearance: int) -> Optional[dict]:
    """The promoted model's call arguments, if this caller may be served by it.

    A model can repeat what it was trained on, so a caller below the training
    data's clearance gets the base model instead.
    """
    try:
        async with driver.session() as session:
            record = await (
                await session.run(
                    """
                    MATCH (a:SystemAgent {agent_id: $kb})
                    RETURN a.answer_model AS model, a.answer_provider AS provider,
                           coalesce(a.answer_model_clearance, 5) AS clearance
                    """,
                    kb=agent_id,
                )
            ).single()
        if record is None or not record["model"] or clearance < record["clearance"]:
            return None
        return get_adapter(record["provider"]).serving(record["model"])
    except Exception as exc:  # noqa: BLE001 — falling back to the base model is always safe
        logger.warning("Answer model unavailable for %s: %s", agent_id, type(exc).__name__)
        return None
