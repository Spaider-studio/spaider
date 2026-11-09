"""
Provider adapters. Each turns "train on this file" into that provider's API.

These were written from public API documentation and tested against reconstructed
fixtures only. No real fine-tuning job has been run through them. Tinker's REST
layout in particular is assumed and needs checking against a real account.
"""
from __future__ import annotations

import asyncio
from dataclasses import dataclass
from pathlib import Path
from typing import Optional, Protocol

import httpx

from app.config import settings

_TIMEOUT = httpx.Timeout(60.0, connect=10.0)


class ProviderNotConfigured(RuntimeError):
    """No API key is set for this provider."""


@dataclass
class ProviderJob:
    status: str  # running | succeeded | failed | cancelled
    model: Optional[str] = None
    error: Optional[str] = None


class Adapter(Protocol):
    name: str

    async def upload(self, path: Path) -> str: ...

    async def create_job(self, file_id: str, base_model: str, hyperparameters: dict) -> str: ...

    async def get_job(self, job_id: str) -> ProviderJob: ...

    async def cancel(self, job_id: str) -> None: ...

    def serving(self, model: str) -> dict: ...


class _Rest:
    name = ""
    base_url = ""
    _api_key = ""

    def __init__(self, transport: Optional[httpx.AsyncBaseTransport] = None) -> None:
        if not self._key():
            raise ProviderNotConfigured(self.name)
        self._transport = transport

    def _key(self) -> str:
        raise NotImplementedError

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            base_url=self.base_url,
            headers={"Authorization": f"Bearer {self._key()}"},
            timeout=_TIMEOUT,
            transport=self._transport,
        )

    async def _request(self, method: str, path: str, **kwargs) -> dict:
        async with self._client() as client:
            response = await client.request(method, path, **kwargs)
        response.raise_for_status()
        return response.json()

    @staticmethod
    async def _read(path: Path) -> bytes:
        return await asyncio.to_thread(path.read_bytes)


class OpenAIAdapter(_Rest):
    name = "openai"
    base_url = "https://api.openai.com/v1"
    _STATES = {
        "validating_files": "running", "queued": "running", "running": "running",
        "succeeded": "succeeded", "failed": "failed", "cancelled": "cancelled",
    }

    def _key(self) -> str:
        return settings.finetune_openai_api_key

    async def upload(self, path: Path) -> str:
        files = {"file": (path.name, await self._read(path), "application/jsonl")}
        return (await self._request("POST", "/files", data={"purpose": "fine-tune"}, files=files))["id"]

    async def create_job(self, file_id: str, base_model: str, hyperparameters: dict) -> str:
        body = {"training_file": file_id, "model": base_model}
        if hyperparameters:
            body["hyperparameters"] = hyperparameters
        return (await self._request("POST", "/fine_tuning/jobs", json=body))["id"]

    async def get_job(self, job_id: str) -> ProviderJob:
        body = await self._request("GET", f"/fine_tuning/jobs/{job_id}")
        error = (body.get("error") or {}).get("message")
        return ProviderJob(self._STATES.get(body["status"], "running"), body.get("fine_tuned_model"), error)

    async def cancel(self, job_id: str) -> None:
        await self._request("POST", f"/fine_tuning/jobs/{job_id}/cancel")

    def serving(self, model: str) -> dict:
        return {"model": model, "api_key": self._key()}


class TogetherAdapter(_Rest):
    name = "together"
    base_url = "https://api.together.xyz/v1"
    _STATES = {
        "pending": "running", "queued": "running", "running": "running", "compressing": "running",
        "uploading": "running", "completed": "succeeded", "failed": "failed", "error": "failed",
        "cancelled": "cancelled", "cancel_requested": "cancelled",
    }

    def _key(self) -> str:
        return settings.finetune_together_api_key

    async def upload(self, path: Path) -> str:
        files = {"file": (path.name, await self._read(path), "application/jsonl")}
        data = {"purpose": "fine-tune", "file_name": path.name}
        return (await self._request("POST", "/files/upload", data=data, files=files))["id"]

    async def create_job(self, file_id: str, base_model: str, hyperparameters: dict) -> str:
        body = {"training_file": file_id, "model": base_model, **hyperparameters}
        return (await self._request("POST", "/fine-tunes", json=body))["id"]

    async def get_job(self, job_id: str) -> ProviderJob:
        body = await self._request("GET", f"/fine-tunes/{job_id}")
        return ProviderJob(self._STATES.get(body["status"], "running"), body.get("output_name"), body.get("message"))

    async def cancel(self, job_id: str) -> None:
        await self._request("POST", f"/fine-tunes/{job_id}/cancel")

    def serving(self, model: str) -> dict:
        return {"model": f"together_ai/{model}", "api_key": self._key()}


class TinkerAdapter(_Rest):
    name = "tinker"

    @property
    def base_url(self) -> str:  # type: ignore[override]
        return settings.finetune_tinker_base_url

    def _key(self) -> str:
        return settings.finetune_tinker_api_key

    async def upload(self, path: Path) -> str:
        files = {"file": (path.name, await self._read(path), "application/jsonl")}
        return (await self._request("POST", "/datasets", files=files))["id"]

    async def create_job(self, file_id: str, base_model: str, hyperparameters: dict) -> str:
        body = {"dataset_id": file_id, "base_model": base_model, "hyperparameters": hyperparameters}
        return (await self._request("POST", "/training_runs", json=body))["id"]

    async def get_job(self, job_id: str) -> ProviderJob:
        body = await self._request("GET", f"/training_runs/{job_id}")
        states = {"completed": "succeeded", "failed": "failed", "cancelled": "cancelled"}
        return ProviderJob(states.get(body["status"], "running"), body.get("model_path"), body.get("error"))

    async def cancel(self, job_id: str) -> None:
        await self._request("POST", f"/training_runs/{job_id}/cancel")

    def serving(self, model: str) -> dict:
        return {"model": f"openai/{model}", "api_key": self._key(), "api_base": self.base_url}


ADAPTERS = {"openai": OpenAIAdapter, "together": TogetherAdapter, "tinker": TinkerAdapter}


def get_adapter(provider: str, transport: Optional[httpx.AsyncBaseTransport] = None) -> Adapter:
    if provider not in ADAPTERS:
        raise KeyError(provider)
    return ADAPTERS[provider](transport=transport)
