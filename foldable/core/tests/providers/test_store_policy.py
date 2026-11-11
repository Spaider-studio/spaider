"""Connection store, projects, sharing policy and ChatService binding/error path
with scripted adapters (no network, no model)."""

from __future__ import annotations

import asyncio

import pytest

from spaider_foldable.providers.connections import ConnectionStore, ModelBinding
from spaider_foldable.providers.credentials import CredentialStore
from spaider_foldable.providers.events import GenerationError, GenerationEvent
from spaider_foldable.providers import policy
from spaider_foldable.providers.presets import load_presets
from spaider_foldable.providers.registry import ProviderRegistry
from spaider_foldable.store.conversations import ConversationStore
from spaider_foldable.store.projects import ProjectStore


def test_connection_store_crud_and_capabilities(db):
    cs = ConnectionStore(db, "prof1")
    c = cs.create(provider="openai_compatible", protocol="chat_completions", name="Mein Server", base_url="http://127.0.0.1:9999/v1/", auth_method="none", execution="local")
    assert c.base_url == "http://127.0.0.1:9999/v1" and c.profile_id == "prof1"
    assert cs.get(c.id).name == "Mein Server" and len(cs.list()) == 1
    c2 = cs.update(c.id, name="Neu", data_sharing="deny", last_test={"ok": True})
    assert c2.name == "Neu" and c2.data_sharing == "deny" and c2.last_test == {"ok": True}
    assert c2.public()["has_credential"] is False and "credential_ref" in c2.public()
    from spaider_foldable.providers.connections import CapabilityProfile

    cs.save_capability(CapabilityProfile(scope=f"connection:{c.id}", model="m1", text="verified", vision="no"))
    assert cs.capability(f"connection:{c.id}", "m1").vision == "no"
    cs.grant_consent(c.id, "default")
    assert cs.consent(c.id, "default") is not None
    assert cs.delete(c.id) and cs.get(c.id) is None and cs.consent(c.id, "default") is None


def test_projects_and_policy_decisions(db):
    cs = ConnectionStore(db)
    ps = ProjectStore(db)
    default = ps.get("default")
    assert default["name"] == "Allgemein" and default["local_only"] is False
    secret = ps.create("Geheim", local_only=True)
    cloud = cs.create(provider="openai", protocol="responses", name="OpenAI", base_url="https://api.openai.com/v1", auth_method="api_key", execution="cloud", data_sharing="ask")
    local = cs.create(provider="ollama", protocol="chat_completions", name="Ollama", base_url="http://127.0.0.1:11434/v1", auth_method="none", execution="local")
    b_cloud = ModelBinding(kind="connection", connection_id=cloud.id, provider_model_id="gpt-x")
    b_local = ModelBinding(kind="connection", connection_id=local.id, provider_model_id="llama3")
    # embedded runtime: always allowed
    assert policy.decide(ModelBinding(kind="local"), secret, cs)[0].allowed
    # local connection: allowed even in a local-only project
    assert policy.decide(b_local, secret, cs)[0].allowed
    # cloud + local-only project: blocked
    d, _ = policy.decide(b_cloud, secret, cs)
    assert not d.allowed and d.code == "local_only"
    # cloud + ask + no consent: consent required
    d, _ = policy.decide(b_cloud, default, cs)
    assert not d.allowed and d.needs_consent and d.code == "consent_required"
    cs.grant_consent(cloud.id, "default")
    assert policy.decide(b_cloud, default, cs)[0].allowed
    cs.revoke_consent(cloud.id, "default")
    assert policy.decide(b_cloud, default, cs)[0].code == "consent_required"
    # deny never
    cs.update(cloud.id, data_sharing="deny")
    assert policy.decide(b_cloud, default, cs)[0].code == "denied"
    # disabled
    cs.update(cloud.id, data_sharing="allow", enabled=False)
    assert policy.decide(b_cloud, default, cs)[0].code == "disabled"
    # unknown connection
    assert policy.decide(ModelBinding(kind="connection", connection_id="nope", provider_model_id="m"), default, cs)[0].code == "unknown_connection"
    # delete project moves conversations back to default
    conv = ConversationStore(db).create("t")
    ConversationStore(db).set_project(conv["id"], secret["id"])
    ps.delete(secret["id"])
    assert ConversationStore(db).get(conv["id"])["project_id"] == "default"
    with pytest.raises(ValueError):
        ps.delete("default")


def test_registry_builds_adapter_with_secret_and_rejects_missing_key(db, tmp_path):
    cs = ConnectionStore(db)
    creds = CredentialStore(tmp_path / "h.json", tmp_path / "v.json")
    reg = ProviderRegistry(load_presets(), cs, creds)
    ref = creds.put("sk-abc", storage="session")
    c = cs.create(provider="mistral", protocol="chat_completions", name="M", base_url="https://api.mistral.ai/v1", auth_method="api_key", credential_ref=ref, credential_storage="session", execution="cloud")
    adapter = asyncio.run(reg.adapter_for(c))
    assert adapter.cfg.api_key == "sk-abc" and adapter.cfg.base_url == "https://api.mistral.ai/v1" and adapter.protocol == "chat_completions"
    assert asyncio.run(reg.adapter_for(c)) is adapter  # cached until the connection changes
    c2 = cs.update(c.id, credential_ref=None)
    with pytest.raises(GenerationError) as exc:
        asyncio.run(reg.adapter_for(c2))
    assert exc.value.code == "credential"
    # presets: a fixed-URL preset is not editable; the generic one is
    presets = load_presets()
    assert presets["openai"].base_url_editable is False and presets["openai_compatible"].base_url_editable is True
    assert "oauth_siwc" in presets["openai_siwc"].auth_methods and "api_key" not in presets["openai_siwc"].auth_methods
    asyncio.run(reg.close_all())


def test_local_presets_file_adds_provider_without_core_changes(tmp_path):
    import json

    (tmp_path / "providers.local.json").write_text(json.dumps([{"id": "mein_dienst", "name": "Mein Dienst", "protocol": "chat_completions", "base_url": "https://llm.example.org/v1", "auth_methods": ["api_key"], "execution": "cloud"}]), encoding="utf-8")
    presets = load_presets(tmp_path / "providers.local.json")
    assert "mein_dienst" in presets and presets["mein_dienst"].protocol == "chat_completions"
    # the embedded runtime cannot be overridden from the local file
    (tmp_path / "providers.local.json").write_text(json.dumps([{"id": "llama_local", "name": "x", "protocol": "llama_local"}]), encoding="utf-8")
    assert load_presets(tmp_path / "providers.local.json")["llama_local"].name.startswith("Eingebettetes")
