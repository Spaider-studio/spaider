"""ProviderConnection records, model bindings, capability profiles and
data-sharing consents (SQLite, schema v4). No secrets are stored here – only an
opaque ``credential_ref`` into the CredentialStore."""

from __future__ import annotations

import json
import secrets
import time
from typing import Any, Literal, Optional

from pydantic import BaseModel, Field, field_validator

from ..db import Database

Execution = Literal["local", "lan", "cloud", "unknown"]
Sharing = Literal["allow", "ask", "deny"]


class ProviderConnection(BaseModel):
    id: str
    profile_id: str = ""
    provider: str  # preset id
    protocol: str
    name: str
    base_url: str
    auth_method: Literal["none", "api_key", "oauth_siwc"] = "api_key"
    credential_ref: Optional[str] = None
    credential_storage: str = "none"  # host | vault | session | none
    account_label: str = ""  # e-mail / account display, never an identity key
    workspace: str = ""
    execution: Execution = "cloud"
    data_sharing: Sharing = "ask"
    default_model: Optional[str] = None
    extra: dict[str, Any] = Field(default_factory=dict)  # provider routing, headers, allow_insecure_http …
    enabled: bool = True
    last_test: Optional[dict] = None
    created_at: str = ""
    updated_at: str = ""

    @field_validator("base_url")
    @classmethod
    def _strip(cls, v: str) -> str:
        return (v or "").strip().rstrip("/")

    def public(self) -> dict:
        d = self.model_dump()
        d["has_credential"] = bool(self.credential_ref)
        return d


class ModelBinding(BaseModel):
    """What a conversation uses for answers: the embedded runtime or a connection + model id."""

    kind: Literal["local", "connection"] = "local"
    connection_id: Optional[str] = None
    provider_model_id: Optional[str] = None
    local_model_id: Optional[str] = None

    def key(self) -> str:
        return "local" if self.kind == "local" else f"{self.connection_id}:{self.provider_model_id}"


class CapabilityProfile(BaseModel):
    """Per (connection or runtime, model) – declared by the preset/provider, verified by tests, or unknown."""

    scope: str  # "connection:<id>" or "local"
    model: str
    text: str = "unknown"
    vision: str = "unknown"
    tools: str = "unknown"
    structured_output: str = "unknown"
    streaming: str = "unknown"
    reasoning: str = "unknown"
    context_length: Optional[int] = None
    parameters: dict[str, str] = Field(default_factory=dict)
    notes_de: str = ""
    checked_at: Optional[str] = None


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


class ConnectionStore:
    def __init__(self, db: Database, profile_id: str = ""):
        self.db = db
        self.profile_id = profile_id

    # ---- connections -----------------------------------------------------------
    def create(self, **fields) -> ProviderConnection:
        now = _now()
        conn = ProviderConnection(id=f"conn_{secrets.token_hex(6)}", profile_id=self.profile_id, created_at=now, updated_at=now, **fields)
        self.db.execute(
            "INSERT INTO provider_connections(id, profile_id, provider, protocol, name, base_url, auth_method, credential_ref, credential_storage, "
            "account_label, workspace, execution, data_sharing, default_model, extra_json, enabled, last_test_json, created_at, updated_at) "
            "VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (
                conn.id, conn.profile_id, conn.provider, conn.protocol, conn.name, conn.base_url, conn.auth_method, conn.credential_ref,
                conn.credential_storage, conn.account_label, conn.workspace, conn.execution, conn.data_sharing, conn.default_model,
                json.dumps(conn.extra, ensure_ascii=False), 1 if conn.enabled else 0, None, now, now,
            ),
        )
        return conn

    def _row(self, row) -> ProviderConnection:
        return ProviderConnection(
            id=row["id"], profile_id=row["profile_id"] or "", provider=row["provider"], protocol=row["protocol"], name=row["name"],
            base_url=row["base_url"], auth_method=row["auth_method"], credential_ref=row["credential_ref"], credential_storage=row["credential_storage"] or "none",
            account_label=row["account_label"] or "", workspace=row["workspace"] or "", execution=row["execution"], data_sharing=row["data_sharing"],
            default_model=row["default_model"], extra=json.loads(row["extra_json"] or "{}"), enabled=bool(row["enabled"]),
            last_test=json.loads(row["last_test_json"]) if row["last_test_json"] else None, created_at=row["created_at"], updated_at=row["updated_at"],
        )

    def get(self, conn_id: str) -> Optional[ProviderConnection]:
        row = self.db.query_one("SELECT * FROM provider_connections WHERE id = ?", (conn_id,))
        return self._row(row) if row else None

    def list(self) -> list[ProviderConnection]:
        return [self._row(r) for r in self.db.query("SELECT * FROM provider_connections ORDER BY created_at")]

    def update(self, conn_id: str, **fields) -> ProviderConnection:
        current = self.get(conn_id)
        if current is None:
            raise KeyError(conn_id)
        data = current.model_dump()
        data.update({k: v for k, v in fields.items() if k in data and k not in ("id", "created_at")})
        data["updated_at"] = _now()
        conn = ProviderConnection.model_validate(data)
        self.db.execute(
            "UPDATE provider_connections SET provider=?, protocol=?, name=?, base_url=?, auth_method=?, credential_ref=?, credential_storage=?, "
            "account_label=?, workspace=?, execution=?, data_sharing=?, default_model=?, extra_json=?, enabled=?, last_test_json=?, updated_at=? WHERE id=?",
            (
                conn.provider, conn.protocol, conn.name, conn.base_url, conn.auth_method, conn.credential_ref, conn.credential_storage, conn.account_label,
                conn.workspace, conn.execution, conn.data_sharing, conn.default_model, json.dumps(conn.extra, ensure_ascii=False), 1 if conn.enabled else 0,
                json.dumps(conn.last_test, ensure_ascii=False) if conn.last_test else None, conn.updated_at, conn.id,
            ),
        )
        return conn

    def delete(self, conn_id: str) -> bool:
        cur = self.db.execute("DELETE FROM provider_connections WHERE id = ?", (conn_id,))
        self.db.execute("DELETE FROM capability_profiles WHERE scope = ?", (f"connection:{conn_id}",))
        self.db.execute("DELETE FROM sharing_consents WHERE connection_id = ?", (conn_id,))
        return cur.rowcount > 0

    # ---- capability profiles --------------------------------------------------------
    def save_capability(self, prof: CapabilityProfile) -> None:
        self.db.execute(
            "INSERT INTO capability_profiles(scope, model, profile_json, checked_at) VALUES (?,?,?,?) "
            "ON CONFLICT(scope, model) DO UPDATE SET profile_json = excluded.profile_json, checked_at = excluded.checked_at",
            (prof.scope, prof.model, prof.model_dump_json(), prof.checked_at or _now()),
        )

    def capability(self, scope: str, model: str) -> Optional[CapabilityProfile]:
        row = self.db.query_one("SELECT profile_json FROM capability_profiles WHERE scope = ? AND model = ?", (scope, model))
        return CapabilityProfile.model_validate_json(row["profile_json"]) if row else None

    def capabilities(self, scope: str) -> list[CapabilityProfile]:
        return [CapabilityProfile.model_validate_json(r["profile_json"]) for r in self.db.query("SELECT profile_json FROM capability_profiles WHERE scope = ?", (scope,))]

    # ---- sharing consents (connection × project) ----------------------------------------
    def consent(self, connection_id: str, project_id: str) -> Optional[dict]:
        row = self.db.query_one("SELECT * FROM sharing_consents WHERE connection_id = ? AND project_id = ?", (connection_id, project_id))
        return dict(row) if row else None

    def grant_consent(self, connection_id: str, project_id: str, *, scope: str = "chat") -> dict:
        now = _now()
        self.db.execute(
            "INSERT INTO sharing_consents(connection_id, project_id, scope, granted_at) VALUES (?,?,?,?) "
            "ON CONFLICT(connection_id, project_id) DO UPDATE SET scope = excluded.scope, granted_at = excluded.granted_at",
            (connection_id, project_id, scope, now),
        )
        return {"connection_id": connection_id, "project_id": project_id, "scope": scope, "granted_at": now}

    def revoke_consent(self, connection_id: str, project_id: str) -> bool:
        return self.db.execute("DELETE FROM sharing_consents WHERE connection_id = ? AND project_id = ?", (connection_id, project_id)).rowcount > 0

    def consents(self) -> list[dict]:
        return [dict(r) for r in self.db.query("SELECT * FROM sharing_consents ORDER BY granted_at")]
