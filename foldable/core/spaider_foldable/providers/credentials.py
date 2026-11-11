"""Credential store with two explicitly different compartments.

Host-bound (default): secrets are protected with Windows DPAPI for the current
user (``CryptProtectData`` via pywin32). The ciphertext lives in
``data/host/credentials.json`` and is useless on another machine or user –
re-entering the key on PC B is the documented behaviour.

Portable vault (opt-in): ``data/profiles/<id>/vault.json`` protected by a user
password: Argon2id (``cryptography``) derives a 32-byte key, AES-256-GCM
authenticates and encrypts the JSON payload; random salt and nonce per write,
versioned header, atomic replace. The vault is unlocked per session only (the
password never touches disk) and locks on profile switch or explicit lock.

What this does NOT do: it encrypts neither the SQLite database nor documents or
model weights, and it cannot protect anything on a compromised host.

References are opaque strings ``host:<id>`` / ``vault:<id>``; records never
contain the secret itself. Nothing here logs secret values.
"""

from __future__ import annotations

import base64
import json
import logging
import os
import secrets
import sys
import tempfile
import threading
import time
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

VAULT_VERSION = 1
# Argon2id parameters: measured on the development laptop (see MODELHUB_TEST_REPORT.md);
# memory 64 MiB keeps unlocking well under a second on weak hardware.
ARGON2_MEMORY_KIB = 64 * 1024
ARGON2_ITERATIONS = 3
ARGON2_LANES = 2


class CredentialError(RuntimeError):
    def __init__(self, code: str, detail: str):
        super().__init__(detail)
        self.code = code
        self.detail = detail


# ---- host-bound (DPAPI) -------------------------------------------------------------
def _dpapi_protect(data: bytes) -> bytes:
    if sys.platform != "win32":
        raise CredentialError("unsupported", "Host-gebundener Schutz ist nur unter Windows (DPAPI) verfügbar.")
    import win32crypt  # type: ignore

    return win32crypt.CryptProtectData(data, "SpAIder Foldable credential", None, None, None, 0)


def _dpapi_unprotect(blob: bytes) -> bytes:
    import win32crypt  # type: ignore

    _desc, data = win32crypt.CryptUnprotectData(blob, None, None, None, 0)
    return data


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=str(path.parent))
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(text)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        try:
            os.unlink(tmp)
        except OSError:
            pass


class HostCredentialStore:
    """``data/host/credentials.json``: {"version":1,"items":{id:{"dpapi": b64, "label":…, "created_at":…}}}"""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()

    def _load(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
            if isinstance(d, dict) and isinstance(d.get("items"), dict):
                return d
        except (OSError, ValueError):
            pass
        return {"version": 1, "items": {}}

    def available(self) -> bool:
        return sys.platform == "win32"

    def put(self, secret: str, *, label: str = "") -> str:
        with self._lock:
            d = self._load()
            cid = secrets.token_hex(8)
            d["items"][cid] = {"dpapi": base64.b64encode(_dpapi_protect(secret.encode("utf-8"))).decode("ascii"), "label": label[:80], "created_at": _now()}
            _atomic_write(self.path, json.dumps(d, ensure_ascii=False, indent=0))
            return f"host:{cid}"

    def get(self, ref: str) -> Optional[str]:
        cid = ref.split(":", 1)[1] if ref.startswith("host:") else ref
        item = self._load()["items"].get(cid)
        if not item:
            return None
        try:
            return _dpapi_unprotect(base64.b64decode(item["dpapi"])).decode("utf-8")
        except Exception as exc:  # noqa: BLE001  (other host / other user / corrupt)
            raise CredentialError("host_bound", "Der Schlüssel ist an einen anderen Rechner oder Benutzer gebunden und hier nicht lesbar.") from exc

    def delete(self, ref: str) -> bool:
        cid = ref.split(":", 1)[1] if ref.startswith("host:") else ref
        with self._lock:
            d = self._load()
            if cid not in d["items"]:
                return False
            del d["items"][cid]
            _atomic_write(self.path, json.dumps(d, ensure_ascii=False, indent=0))
            return True

    def labels(self) -> dict[str, dict]:
        return {f"host:{k}": {"label": v.get("label", ""), "created_at": v.get("created_at")} for k, v in self._load()["items"].items()}


# ---- portable vault (Argon2id + AES-256-GCM) ------------------------------------------
def _derive_key(password: str, salt: bytes, *, memory_kib: int, iterations: int, lanes: int) -> bytes:
    from cryptography.hazmat.primitives.kdf.argon2 import Argon2id

    kdf = Argon2id(salt=salt, length=32, iterations=iterations, lanes=lanes, memory_cost=memory_kib)
    return kdf.derive(password.encode("utf-8"))


class PortableVault:
    """``vault.json``: {"version":1,"kdf":{...},"salt":b64,"nonce":b64,"ciphertext":b64,"updated_at":…}
    The plaintext is a JSON object {"items": {id: {"secret": …, "label": …, "created_at": …}}}."""

    def __init__(self, path: Path):
        self.path = path
        self._lock = threading.Lock()
        self._key: Optional[bytes] = None
        self._items: Optional[dict] = None
        self._kdf_params = {"memory_kib": ARGON2_MEMORY_KIB, "iterations": ARGON2_ITERATIONS, "lanes": ARGON2_LANES}

    # -- state --------------------------------------------------------------
    def exists(self) -> bool:
        return self.path.exists()

    @property
    def unlocked(self) -> bool:
        return self._key is not None and self._items is not None

    def lock(self) -> None:
        with self._lock:
            self._key = None
            self._items = None

    def status(self) -> dict:
        return {"exists": self.exists(), "unlocked": self.unlocked, "path": str(self.path), "kdf": self._kdf_params if self.exists() else None}

    # -- crypto -------------------------------------------------------------------
    def _read_header(self) -> dict:
        try:
            d = json.loads(self.path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise CredentialError("vault_corrupt", "Der Tresor ist beschädigt oder nicht lesbar.") from exc
        if not isinstance(d, dict) or d.get("version") != VAULT_VERSION or not all(k in d for k in ("kdf", "salt", "nonce", "ciphertext")):
            raise CredentialError("vault_corrupt", "Der Tresor hat ein unbekanntes Format.")
        return d

    def create(self, password: str) -> None:
        if len(password) < 8:
            raise CredentialError("weak_password", "Das Tresor-Passwort braucht mindestens 8 Zeichen.")
        if self.exists():
            raise CredentialError("exists", "Es gibt bereits einen Tresor.")
        with self._lock:
            salt = secrets.token_bytes(16)
            self._key = _derive_key(password, salt, **self._kdf_params)
            self._items = {}
            self._write_locked(salt)

    def unlock(self, password: str) -> dict:
        from cryptography.exceptions import InvalidTag
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        t0 = time.monotonic()
        d = self._read_header()
        kdf = d["kdf"]
        salt = base64.b64decode(d["salt"])
        key = _derive_key(password, salt, memory_kib=int(kdf["memory_kib"]), iterations=int(kdf["iterations"]), lanes=int(kdf["lanes"]))
        try:
            plain = AESGCM(key).decrypt(base64.b64decode(d["nonce"]), base64.b64decode(d["ciphertext"]), _aad(d))
        except InvalidTag as exc:
            raise CredentialError("wrong_password", "Falsches Passwort oder manipulierter Tresor.") from exc
        try:
            obj = json.loads(plain.decode("utf-8"))
        except ValueError as exc:
            raise CredentialError("vault_corrupt", "Tresorinhalt ist beschädigt.") from exc
        with self._lock:
            self._key = key
            self._items = obj.get("items", {})
            self._kdf_params = {"memory_kib": int(kdf["memory_kib"]), "iterations": int(kdf["iterations"]), "lanes": int(kdf["lanes"])}
        return {"items": len(self._items), "unlock_ms": int((time.monotonic() - t0) * 1000)}

    def _write_locked(self, salt: Optional[bytes] = None) -> None:
        from cryptography.hazmat.primitives.ciphers.aead import AESGCM

        assert self._key is not None and self._items is not None
        if salt is None:
            salt = base64.b64decode(self._read_header()["salt"])
        nonce = secrets.token_bytes(12)
        header = {"version": VAULT_VERSION, "kdf": {"name": "argon2id", **self._kdf_params}, "salt": base64.b64encode(salt).decode(), "nonce": base64.b64encode(nonce).decode(), "updated_at": _now()}
        ct = AESGCM(self._key).encrypt(nonce, json.dumps({"items": self._items}, ensure_ascii=False).encode("utf-8"), _aad(header))
        header["ciphertext"] = base64.b64encode(ct).decode()
        _atomic_write(self.path, json.dumps(header, ensure_ascii=False))

    def _require(self) -> None:
        if not self.unlocked:
            raise CredentialError("locked", "Der portable Tresor ist gesperrt; bitte Passwort eingeben.")

    def put(self, secret: str, *, label: str = "") -> str:
        with self._lock:
            self._require()
            cid = secrets.token_hex(8)
            self._items[cid] = {"secret": secret, "label": label[:80], "created_at": _now()}
            self._write_locked()
            return f"vault:{cid}"

    def get(self, ref: str) -> Optional[str]:
        cid = ref.split(":", 1)[1] if ref.startswith("vault:") else ref
        self._require()
        item = (self._items or {}).get(cid)
        return item["secret"] if item else None

    def delete(self, ref: str) -> bool:
        cid = ref.split(":", 1)[1] if ref.startswith("vault:") else ref
        with self._lock:
            self._require()
            if cid not in self._items:
                return False
            del self._items[cid]
            self._write_locked()
            return True

    def labels(self) -> dict[str, dict]:
        if not self.unlocked:
            return {}
        return {f"vault:{k}": {"label": v.get("label", ""), "created_at": v.get("created_at")} for k, v in self._items.items()}

    def change_password(self, old: str, new: str) -> None:
        self.unlock(old)
        if len(new) < 8:
            raise CredentialError("weak_password", "Das Tresor-Passwort braucht mindestens 8 Zeichen.")
        with self._lock:
            salt = secrets.token_bytes(16)
            self._key = _derive_key(new, salt, **self._kdf_params)
            self._write_locked(salt)


def _aad(header: dict) -> bytes:
    kdf = header["kdf"]
    return f"spaider-vault-v{header['version']}:{kdf.get('memory_kib')}:{kdf.get('iterations')}:{kdf.get('lanes')}".encode("ascii")


class CredentialStore:
    """Facade: routes references to the host store or the portable vault."""

    def __init__(self, host_path: Path, vault_path: Path, *, memory_only: bool = False):
        self.host = HostCredentialStore(host_path)
        self.vault = PortableVault(vault_path)
        self._session: dict[str, str] = {}  # ``session:<id>`` secrets, never written
        self.memory_only = memory_only

    def put(self, secret: str, *, storage: str = "host", label: str = "") -> str:
        secret = (secret or "").strip()
        if not secret:
            raise CredentialError("empty", "Es wurde kein Schlüssel angegeben.")
        if storage == "session" or self.memory_only:
            cid = secrets.token_hex(8)
            self._session[cid] = secret
            return f"session:{cid}"
        if storage == "vault":
            return self.vault.put(secret, label=label)
        if storage == "host":
            if not self.host.available():
                raise CredentialError("unsupported", "Host-gebundener Schutz nicht verfügbar; Tresor oder Sitzungsspeicher wählen.")
            return self.host.put(secret, label=label)
        raise CredentialError("bad_storage", f"Unbekannter Speicherort: {storage}")

    def get(self, ref: Optional[str]) -> Optional[str]:
        if not ref:
            return None
        if ref.startswith("session:"):
            return self._session.get(ref.split(":", 1)[1])
        if ref.startswith("vault:"):
            return self.vault.get(ref)
        if ref.startswith("host:"):
            return self.host.get(ref)
        raise CredentialError("bad_ref", "Unbekannte Schlüsselreferenz.")

    def delete(self, ref: Optional[str]) -> bool:
        if not ref:
            return False
        if ref.startswith("session:"):
            return self._session.pop(ref.split(":", 1)[1], None) is not None
        if ref.startswith("vault:"):
            return self.vault.delete(ref)
        if ref.startswith("host:"):
            return self.host.delete(ref)
        return False

    def storage_of(self, ref: Optional[str]) -> str:
        return (ref or "none").split(":", 1)[0] if ref else "none"

    def lock_all(self) -> None:
        self._session.clear()
        self.vault.lock()

    def status(self) -> dict:
        return {"host_available": self.host.available(), "vault": self.vault.status(), "session_items": len(self._session)}


def _now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
