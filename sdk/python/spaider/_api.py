"""Endpoints shared by the sync and async clients.

``_request`` is a plain call on one client and a coroutine on the other, so each
method here simply returns what it gives back.
"""
from __future__ import annotations

from typing import Any, Dict, List, Mapping, Optional


def _clean(**values: Any) -> Dict[str, Any]:
    return {key: value for key, value in values.items() if value is not None}


class MemoryApi:
    agent_id: str

    def remember(self, content: str, **fields: Any) -> Any:
        """Store an observation with no LLM call. Accepts the /episodes fields."""
        return self.remember_many([{"content": content, **fields}])

    def remember_many(self, episodes: List[Mapping[str, Any]]) -> Any:
        return self._request("POST", "/api/v1/episodes", json={"agent_id": self.agent_id, "episodes": list(episodes)})

    def recall(self, query: str = "", *, limit: int = 10, federated: bool = False, **filters: Any) -> Any:
        """Search memory with no LLM call."""
        body = {"agent_id": self.agent_id, "query": query, "limit": limit, "federated": federated, **filters}
        return self._request("POST", "/api/v1/recall", json=body)

    def feedback(self, query_id: str, success: bool, node_ids: Optional[List[str]] = None) -> Any:
        """Rate an answer. Reversing a verdict replaces the earlier one."""
        body = {"query_id": query_id, "success": success, "agent_id": self.agent_id, "used_node_ids": node_ids or []}
        return self._request("POST", "/api/v1/system/feedback", json=body)

    def sleep(self) -> Any:
        """Run the consolidation cycle now."""
        return self._request("POST", f"/api/v1/agents/{self.agent_id}/sleep")

    def sleep_report(self) -> Any:
        return self._request("GET", f"/api/v1/agents/{self.agent_id}/sleep/report")

    def snapshot(self) -> Any:
        return self._request("GET", f"/api/v1/agents/{self.agent_id}/snapshot")

    # ── Federation ────────────────────────────────────────────────────────

    def request_grant(
        self,
        source_kb: str,
        *,
        target_kb: Optional[str] = None,
        node_types: Optional[List[str]] = None,
        relation_types: Optional[List[str]] = None,
        max_clearance: int = 1,
        expires_at: Optional[str] = None,
    ) -> Any:
        """Ask to read another KB. A steward of that KB must approve it."""
        body = _clean(
            source_kb=source_kb,
            target_kb=target_kb or self.agent_id,
            node_types=node_types,
            relation_types=relation_types,
            max_clearance=max_clearance,
            expires_at=expires_at,
        )
        return self._request("POST", "/api/v1/federation/grants", json=body)

    def list_grants(self) -> Any:
        return self._request("GET", "/api/v1/federation/grants")

    def approve_grant(self, grant_id: str, **narrowed: Any) -> Any:
        return self._request("POST", f"/api/v1/federation/grants/{grant_id}/approve", json=narrowed)

    def deny_grant(self, grant_id: str) -> Any:
        return self._request("POST", f"/api/v1/federation/grants/{grant_id}/deny")

    def revoke_grant(self, grant_id: str) -> Any:
        return self._request("DELETE", f"/api/v1/federation/grants/{grant_id}")

    def create_kpr(self, source_kb: str, node_ids: List[str], title: str, *, target_kb: Optional[str] = None) -> Any:
        body = {"source_kb": source_kb, "target_kb": target_kb or self.agent_id, "node_ids": node_ids, "title": title}
        return self._request("POST", "/api/v1/federation/kpr", json=body)

    def list_kprs(self, target_kb: Optional[str] = None) -> Any:
        return self._request("GET", "/api/v1/federation/kpr", params={"target_kb": target_kb or self.agent_id})

    def get_kpr(self, kpr_id: str) -> Any:
        return self._request("GET", f"/api/v1/federation/kpr/{kpr_id}")

    def approve_kpr(self, kpr_id: str) -> Any:
        return self._request("POST", f"/api/v1/federation/kpr/{kpr_id}/approve")

    def reject_kpr(self, kpr_id: str) -> Any:
        return self._request("POST", f"/api/v1/federation/kpr/{kpr_id}/reject")

    def revert_kpr(self, kpr_id: str) -> Any:
        return self._request("POST", f"/api/v1/federation/kpr/{kpr_id}/revert")

    # ── Fine-tuning ───────────────────────────────────────────────────────

    def create_finetune(self, provider: str, base_model: str, **options: Any) -> Any:
        """Submit a training job. Training data is sent to the provider."""
        body = {"agent_id": self.agent_id, "provider": provider, "base_model": base_model, **options}
        return self._request("POST", "/api/v1/finetune/jobs", json=body)

    def list_finetunes(self) -> Any:
        return self._request("GET", "/api/v1/finetune/jobs", params={"agent_id": self.agent_id})

    def finetune_status(self, version_id: str) -> Any:
        return self._request("GET", f"/api/v1/finetune/jobs/{version_id}")

    def evaluate_finetune(self, version_id: str, items: List[Mapping[str, str]], **gate: Any) -> Any:
        body = {"items": list(items), **gate}
        return self._request("POST", f"/api/v1/finetune/jobs/{version_id}/evaluate", json=body)

    def promote_finetune(self, version_id: str) -> Any:
        return self._request("POST", f"/api/v1/finetune/jobs/{version_id}/promote")

    def rollback_finetune(self) -> Any:
        return self._request("POST", "/api/v1/finetune/rollback", params={"agent_id": self.agent_id})
