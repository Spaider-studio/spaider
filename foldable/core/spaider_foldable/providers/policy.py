"""Data-sharing policy: may a binding be used for a given project?

Rules (ADR-8):
* the embedded runtime (``local``) and connections with ``execution == "local"``
  are always allowed;
* a project marked ``local_only`` blocks every other connection, including
  LAN servers ("unknown" processing is not local);
* connections with ``data_sharing == "deny"`` are never used for generation;
* ``allow`` needs no consent; ``ask`` needs a stored consent for (connection,
  project) – the UI asks once and the consent is visible and revocable.
Background jobs (extraction, embeddings, titles) never use cloud bindings;
they stay on the embedded runtime regardless of the chat binding.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from .connections import ConnectionStore, ModelBinding, ProviderConnection


@dataclass
class PolicyDecision:
    allowed: bool
    reason_de: str = ""
    needs_consent: bool = False
    code: str = "ok"  # ok | local_only | denied | consent_required | disabled | unknown_connection

    def to_dict(self) -> dict:
        return {"allowed": self.allowed, "reason_de": self.reason_de, "needs_consent": self.needs_consent, "code": self.code}


def decide(binding: ModelBinding, project: Optional[dict], connections: ConnectionStore) -> tuple[PolicyDecision, Optional[ProviderConnection]]:
    if binding.kind == "local":
        return PolicyDecision(True), None
    conn = connections.get(binding.connection_id or "")
    if conn is None:
        return PolicyDecision(False, "Die gewählte Verbindung existiert nicht mehr.", code="unknown_connection"), None
    if not conn.enabled:
        return PolicyDecision(False, f"Die Verbindung „{conn.name}“ ist deaktiviert.", code="disabled"), conn
    if conn.execution == "local":
        return PolicyDecision(True), conn
    local_only = bool((project or {}).get("local_only"))
    if local_only:
        return PolicyDecision(False, f"Das Projekt „{(project or {}).get('name', '')}“ ist als „Nur lokal“ markiert; „{conn.name}“ verarbeitet außerhalb dieses Rechners ({conn.execution}).", code="local_only"), conn
    if conn.data_sharing == "deny":
        return PolicyDecision(False, f"Für „{conn.name}“ ist die Datenfreigabe auf „nie“ gesetzt.", code="denied"), conn
    if conn.data_sharing == "allow":
        return PolicyDecision(True), conn
    pid = (project or {}).get("id", "default")
    if connections.consent(conn.id, pid):
        return PolicyDecision(True), conn
    return PolicyDecision(False, f"Bevor Inhalte des Projekts „{(project or {}).get('name', 'Allgemein')}“ an „{conn.name}“ ({conn.execution}) gesendet werden, ist eine einmalige Freigabe nötig.", needs_consent=True, code="consent_required"), conn


def sharing_preview(messages: list[dict], memory_chars: int, sources: list[dict]) -> dict:
    """What would be sent: counts only, no content (the UI shows the counts and lets the user open the context)."""
    return {
        "history_messages": sum(1 for m in messages if m.get("role") in ("user", "assistant")),
        "system_prompt": any(m.get("role") == "system" for m in messages),
        "memory_chars": memory_chars,
        "sources": len(sources),
        "images": sum(1 for m in messages if isinstance(m.get("content"), list) and any(p.get("type") == "image_url" for p in m["content"])),
    }
