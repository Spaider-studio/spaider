"""HTTP client for the SpAIder REST API.

Thin wrapper over httpx that exposes only the endpoints the CLI needs. All
calls target ``${SPAIDER_API_BASE:-http://localhost:8000/api/v1}`` by default.

The local dev API does not require authentication for /agents endpoints; the
client therefore makes unauthenticated calls. If the user is targeting a
deployment behind Kong with auth enforced (production / managed Cloud), a
future ``--api-key`` flag on the CLI will be plumbed through here.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from typing import Any

import httpx

DEFAULT_API_BASE = "http://localhost:8000/api/v1"


def api_base() -> str:
    """Resolve the API base URL from the environment, with a localhost default."""
    return os.environ.get("SPAIDER_API_BASE", DEFAULT_API_BASE).rstrip("/")


class SpaiderApiError(RuntimeError):
    """Raised when the SpAIder API returns a non-2xx response or is unreachable."""


@dataclass
class Agent:
    """Subset of the SpAIder Agent shape that the CLI cares about."""
    id: str
    name: str
    api_key: str | None = None  # only populated on create / rotate
    description: str | None = None
    tenant_id: str = "default"
    clearance_level: int = 1

    @classmethod
    def from_payload(cls, data: dict[str, Any]) -> "Agent":
        return cls(
            id=data.get("id", ""),
            name=data.get("name", ""),
            api_key=data.get("api_key"),
            description=data.get("description"),
            tenant_id=data.get("tenant_id", "default"),
            clearance_level=int(data.get("clearance_level", 1)),
        )


def _client(timeout: float = 10.0) -> httpx.Client:
    return httpx.Client(base_url=api_base(), timeout=timeout)


def health() -> bool:
    """Return True iff the SpAIder API is reachable and reports healthy.

    Uses a short timeout so the doctor command doesn't hang when the backend
    isn't running.
    """
    try:
        with httpx.Client(timeout=2.0) as c:
            resp = c.get(f"{api_base().rsplit('/api', 1)[0]}/health")
            return resp.status_code == 200 and resp.json().get("healthy") is True
    except Exception:
        return False


def embedding_health() -> dict | None:
    """GET /health/embedding — embedding-dimension consistency report.

    Returns the report dict (``expected_dims``/``present_dims``/``consistent``…)
    or ``None`` when the backend isn't reachable. Slightly longer timeout than
    ``health()`` since the backend scans for distinct vector sizes.
    """
    try:
        with httpx.Client(timeout=5.0) as c:
            resp = c.get(f"{api_base().rsplit('/api', 1)[0]}/health/embedding")
            if resp.status_code == 200:
                return resp.json()
    except Exception:
        return None
    return None


def list_agents() -> list[Agent]:
    """GET /agents — list every agent."""
    try:
        with _client() as c:
            resp = c.get("/agents")
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise SpaiderApiError(f"list_agents failed: {exc}") from exc
    return [Agent.from_payload(a) for a in payload.get("agents", [])]


def find_agent_by_name(name: str) -> Agent | None:
    """Return the first agent whose name matches, or None."""
    for a in list_agents():
        if a.name == name:
            return a
    return None


def create_agent(
    name: str,
    *,
    description: str | None = None,
    tenant_id: str = "default",
    clearance_level: int = 1,
) -> Agent:
    """POST /agents — creates a new agent. Returns the created agent (with api_key)."""
    body: dict[str, Any] = {
        "name": name,
        "tenant_id": tenant_id,
        "permissions": ["read", "write", "query"],
        "clearance_level": clearance_level,
    }
    if description:
        body["description"] = description
    try:
        with _client() as c:
            resp = c.post("/agents", json=body)
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise SpaiderApiError(f"create_agent({name!r}) failed: {exc}") from exc
    return Agent.from_payload(payload.get("agent", {}))


def rotate_key(agent_id: str) -> str:
    """POST /agents/{id}/rotate-key — returns the new raw API key.

    The key is shown exactly once; the backend only stores its SHA-256 hash
    after this call returns.
    """
    try:
        with _client() as c:
            resp = c.post(f"/agents/{agent_id}/rotate-key")
            resp.raise_for_status()
            payload = resp.json()
    except httpx.HTTPError as exc:
        raise SpaiderApiError(f"rotate_key({agent_id}) failed: {exc}") from exc
    key = payload.get("api_key")
    if not key:
        raise SpaiderApiError(f"rotate_key({agent_id}) returned no api_key")
    return key


def delete_agent(agent_id: str) -> None:
    """DELETE /agents/{id} — removes the agent and all its graph data."""
    try:
        with _client() as c:
            resp = c.delete(f"/agents/{agent_id}")
            resp.raise_for_status()
    except httpx.HTTPError as exc:
        raise SpaiderApiError(f"delete_agent({agent_id}) failed: {exc}") from exc
