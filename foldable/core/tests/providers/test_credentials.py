"""Credential store (T13): host-bound DPAPI round trip, portable vault with wrong
password, tampered ciphertext/header, corrupt file, lock, no plaintext on disk."""

from __future__ import annotations

import base64
import json
import sys
import time

import pytest

from spaider_foldable.providers.credentials import CredentialError, CredentialStore, PortableVault


def test_vault_create_unlock_put_get_lock(tmp_path):
    v = PortableVault(tmp_path / "vault.json")
    assert not v.exists() and not v.unlocked
    v.create("korrektes-passwort")
    ref = v.put("sk-geheim-123", label="test")
    assert ref.startswith("vault:")
    raw = (tmp_path / "vault.json").read_text(encoding="utf-8")
    assert "sk-geheim-123" not in raw and "korrektes-passwort" not in raw
    header = json.loads(raw)
    assert header["version"] == 1 and header["kdf"]["name"] == "argon2id"
    v.lock()
    with pytest.raises(CredentialError) as exc:
        v.get(ref)
    assert exc.value.code == "locked"
    t0 = time.monotonic()
    info = v.unlock("korrektes-passwort")
    unlock_s = time.monotonic() - t0
    assert info["items"] == 1 and v.get(ref) == "sk-geheim-123"
    assert unlock_s < 5.0  # Argon2id parameters stay usable on weak hardware (measured in the report)


def test_vault_wrong_password_and_tamper(tmp_path):
    p = tmp_path / "vault.json"
    v = PortableVault(p)
    v.create("passwort-eins")
    ref = v.put("secret")
    v.lock()
    with pytest.raises(CredentialError) as exc:
        v.unlock("passwort-zwei")
    assert exc.value.code == "wrong_password"
    # tampered ciphertext
    d = json.loads(p.read_text(encoding="utf-8"))
    ct = bytearray(base64.b64decode(d["ciphertext"]))
    ct[5] ^= 0xFF
    d["ciphertext"] = base64.b64encode(bytes(ct)).decode()
    p.write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(CredentialError) as exc:
        v.unlock("passwort-eins")
    assert exc.value.code == "wrong_password"  # authenticated encryption: tamper == wrong key
    # tampered header (kdf params are authenticated data)
    v2 = PortableVault(tmp_path / "v2.json")
    v2.create("passwort-eins")
    v2.put("s")
    d = json.loads((tmp_path / "v2.json").read_text(encoding="utf-8"))
    d["kdf"]["iterations"] = 1
    (tmp_path / "v2.json").write_text(json.dumps(d), encoding="utf-8")
    with pytest.raises(CredentialError):
        PortableVault(tmp_path / "v2.json").unlock("passwort-eins")
    # corrupt file
    (tmp_path / "v3.json").write_text("{not json", encoding="utf-8")
    with pytest.raises(CredentialError) as exc:
        PortableVault(tmp_path / "v3.json").unlock("x")
    assert exc.value.code == "vault_corrupt"
    assert ref


def test_vault_change_password_and_atomic_file(tmp_path):
    p = tmp_path / "vault.json"
    v = PortableVault(p)
    v.create("altes-passwort")
    ref = v.put("wert")
    v.change_password("altes-passwort", "neues-passwort")
    v.lock()
    with pytest.raises(CredentialError):
        v.unlock("altes-passwort")
    v.unlock("neues-passwort")
    assert v.get(ref) == "wert"
    assert not list(tmp_path.glob("vault.json.*.tmp"))  # atomic replace leaves no temp files


@pytest.mark.skipif(sys.platform != "win32", reason="DPAPI only on Windows")
def test_host_store_roundtrip_and_delete(tmp_path):
    store = CredentialStore(tmp_path / "host" / "credentials.json", tmp_path / "vault.json")
    ref = store.put("api-key-xyz", storage="host", label="t")
    assert ref.startswith("host:") and store.get(ref) == "api-key-xyz"
    raw = (tmp_path / "host" / "credentials.json").read_text(encoding="utf-8")
    assert "api-key-xyz" not in raw
    assert store.delete(ref) and store.get(ref) is None
    assert store.storage_of(ref) == "host"


def test_session_storage_never_touches_disk(tmp_path):
    store = CredentialStore(tmp_path / "host" / "credentials.json", tmp_path / "vault.json")
    ref = store.put("tmp-secret", storage="session")
    assert store.get(ref) == "tmp-secret" and not (tmp_path / "host").exists()
    store.lock_all()
    assert store.get(ref) is None


def test_empty_secret_and_bad_ref(tmp_path):
    store = CredentialStore(tmp_path / "h.json", tmp_path / "v.json")
    with pytest.raises(CredentialError):
        store.put("   ", storage="session")
    with pytest.raises(CredentialError):
        store.get("weird:ref")
