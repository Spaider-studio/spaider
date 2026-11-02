"""Installed models: on-disk manifests (source of truth) mirrored into SQLite.

Layout: ``data/models/<model_id>/<file>.gguf`` + ``manifest.json``. On every
start the models directory is reconciled with the ``models`` table so a
manually copied model directory or a rebuilt database still works.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from threading import Event
from typing import Callable, Optional

from ..db import Database
from ..paths import PortablePaths
from .catalog import CatalogEntry
from .gguf import GGUFError, GGUFInfo, is_architecture_supported, read_gguf_info

logger = logging.getLogger(__name__)

MANIFEST = "manifest.json"
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,80}$")


def utcnow() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%S.%f")[:-3] + "Z"


class ModelValidationError(ValueError):
    def __init__(self, code: str, detail: str):
        self.code = code
        self.detail = detail
        super().__init__(detail)


def validate_gguf(path: Path, *, expected_role: Optional[str] = None) -> GGUFInfo:
    try:
        info = read_gguf_info(path)
    except GGUFError as exc:
        raise ModelValidationError("gguf_invalid", f"Die Datei ist keine gültige GGUF-Modelldatei: {exc}") from exc
    except OSError as exc:
        raise ModelValidationError("gguf_invalid", f"Datei nicht lesbar: {exc}") from exc
    if not is_architecture_supported(info.architecture):
        raise ModelValidationError(
            "arch_unsupported",
            f"Die Modellarchitektur „{info.architecture}“ wird vom gebündelten llama.cpp-Build nicht unterstützt.",
        )
    role = "embedding" if info.is_embedding_model else "chat"
    if expected_role and role != expected_role:
        raise ModelValidationError(
            "role_mismatch",
            f"Die Datei ist ein {'Embedding' if role == 'embedding' else 'Chat'}-Modell, erwartet wurde {expected_role}.",  # noqa: E501
        )
    return info


def model_role_from_info(info: GGUFInfo) -> str:
    return "embedding" if info.is_embedding_model else "chat"


class ModelRegistry:
    def __init__(self, db: Database, paths: PortablePaths):
        self.db = db
        self.paths = paths

    # ---- queries ----------------------------------------------------------
    def list(self) -> list[dict]:
        rows = self.db.query("SELECT * FROM models ORDER BY created_at")
        active = self.active_ids()
        return [self._row_to_api(r, active) for r in rows]

    def get(self, model_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM models WHERE id = ?", (model_id,))
        return self._row_to_api(row, self.active_ids()) if row else None

    def find_by_catalog(self, catalog_id: str) -> dict | None:
        row = self.db.query_one("SELECT * FROM models WHERE catalog_id = ?", (catalog_id,))
        return self._row_to_api(row, self.active_ids()) if row else None

    def abs_path(self, model: dict) -> Path:
        return self.paths.from_relative(model["rel_path"])

    def active_ids(self) -> dict[str, Optional[str]]:
        return {
            "chat": self.get_state("active_chat_model_id"),
            "embedding": self.get_state("active_embedding_model_id"),
        }

    def get_state(self, key: str):
        raw = self.db.scalar("SELECT value_json FROM model_state WHERE key = ?", (key,))
        return json.loads(raw) if raw is not None else None

    def set_state(self, key: str, value) -> None:
        self.db.execute(
            "INSERT INTO model_state(key, value_json) VALUES (?, ?) "
            "ON CONFLICT(key) DO UPDATE SET value_json = excluded.value_json",
            (key, json.dumps(value)),
        )

    # ---- install ----------------------------------------------------------
    def install_from_download(self, entry: CatalogEntry, part_path: Path, *, sha_verified: bool) -> dict:
        model_id = self._new_id(entry.id)
        target_dir = self.paths.models_dir / model_id
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / entry.files[0].path
        # Same volume: rename is atomic. Verified bytes only.
        part_path.replace(target)
        info = validate_gguf(target, expected_role=entry.role)
        manifest = self._manifest(
            model_id=model_id,
            name=entry.name,
            role=entry.role,
            catalog_id=entry.id,
            source="catalog",
            file=target,
            sha256=entry.files[0].sha256,
            sha_verified=sha_verified,
            info=info,
            license=entry.license.model_dump(),
            context_length=entry.context_length,
            embedding_dimensions=entry.embedding_dimensions,
        )
        self._write_manifest(target_dir, manifest)
        self._upsert(manifest)
        return self.get(model_id)  # type: ignore[return-value]

    def import_local(
        self,
        src: Path,
        *,
        progress: Optional[Callable[[int, int], None]] = None,
        cancel: Optional[Event] = None,
        role: Optional[str] = None,
    ) -> dict:
        src = Path(src)
        if not src.is_file():
            raise ModelValidationError("gguf_invalid", "Die angegebene Datei existiert nicht.")
        info = validate_gguf(src)
        detected_role = model_role_from_info(info)
        if role and role != detected_role:
            raise ModelValidationError("role_mismatch", "Die Datei passt nicht zur gewählten Rolle.")
        role = detected_role
        size = src.stat().st_size
        if self.paths.is_inside_data(src):
            raise ModelValidationError("gguf_invalid", "Die Datei liegt bereits im Datenverzeichnis.")
        free = shutil.disk_usage(self.paths.models_dir).free
        if free < size + 64 * (1 << 20):
            raise ModelValidationError("insufficient_space", "Nicht genug freier Speicher zum Kopieren des Modells.")
        model_id = self._new_id(_slug(src.stem))
        target_dir = self.paths.models_dir / model_id
        target_dir.mkdir(parents=True, exist_ok=False)
        target = target_dir / src.name
        tmp = target.with_suffix(target.suffix + ".part")
        h = hashlib.sha256()
        done = 0
        try:
            with open(src, "rb") as fin, open(tmp, "wb") as fout:
                while True:
                    if cancel is not None and cancel.is_set():
                        raise ModelValidationError("cancelled", "Import abgebrochen.")
                    block = fin.read(1 << 20)
                    if not block:
                        break
                    fout.write(block)
                    h.update(block)
                    done += len(block)
                    if progress:
                        progress(done, size)
            tmp.replace(target)
        except BaseException:
            shutil.rmtree(target_dir, ignore_errors=True)
            raise
        manifest = self._manifest(
            model_id=model_id,
            name=info.name or src.stem,
            role=role,
            catalog_id=None,
            source="import",
            file=target,
            sha256=h.hexdigest(),
            sha_verified=True,
            info=info,
            license=None,
            context_length=info.context_length or 4096,
            embedding_dimensions=info.embedding_length if role == "embedding" else None,
        )
        self._write_manifest(target_dir, manifest)
        self._upsert(manifest)
        return self.get(model_id)  # type: ignore[return-value]

    def remove(self, model_id: str) -> None:
        model = self.get(model_id)
        if model is None:
            return
        target_dir = self.paths.models_dir / model_id
        if self.paths.is_inside_data(target_dir) and target_dir.exists():
            shutil.rmtree(target_dir, ignore_errors=False)
        self.db.execute("DELETE FROM models WHERE id = ?", (model_id,))

    # ---- reconcile ----------------------------------------------------------
    def reconcile(self) -> None:
        """Make the DB mirror the manifests on disk; flag broken installs."""
        seen: set[str] = set()
        for d in sorted(self.paths.models_dir.iterdir() if self.paths.models_dir.exists() else []):
            mf = d / MANIFEST
            if not d.is_dir() or not mf.exists():
                continue
            try:
                manifest = json.loads(mf.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                logger.warning("unreadable manifest %s: %s", mf, exc)
                continue
            file_path = d / manifest["file"]
            broken = not file_path.exists() or file_path.stat().st_size != int(manifest["size_bytes"])
            if not broken and "has_chat_template" not in (manifest.get("metadata") or {}):
                # manifest written by 0.1.0: capture template facts once (header read only)
                try:
                    info = read_gguf_info(file_path)
                    meta = dict(manifest.get("metadata") or {})
                    meta.update({k: v for k, v in info.metadata.items() if isinstance(v, (str, int, float, bool))})
                    meta["has_chat_template"] = info.has_chat_template
                    meta["chat_template_excerpt"] = (info.chat_template or "")[:4000]
                    manifest["metadata"] = meta
                    self._write_manifest(d, manifest)
                except (GGUFError, OSError) as exc:
                    logger.warning("could not refresh metadata for %s: %s", d.name, exc)
            manifest["state"] = "broken" if broken else "ready"
            manifest["rel_path"] = self.paths.relative_to_data(file_path)
            self._upsert(manifest)
            seen.add(manifest["id"])
        for row in self.db.query("SELECT id FROM models"):
            if row["id"] not in seen:
                self.db.execute("DELETE FROM models WHERE id = ?", (row["id"],))
        for key in ("active_chat_model_id", "active_embedding_model_id"):
            mid = self.get_state(key)
            if mid and (mid not in seen or self.db.scalar("SELECT state FROM models WHERE id = ?", (mid,)) != "ready"):
                logger.warning("active model %s (%s) is missing or broken; clearing", mid, key)
                self.set_state(key, None)

    # ---- helpers ----------------------------------------------------------
    def _new_id(self, base: str) -> str:
        base = _slug(base) or "model"
        candidate = base
        n = 1
        while (self.paths.models_dir / candidate).exists() or self.db.query_one(
            "SELECT 1 FROM models WHERE id = ?", (candidate,)
        ):
            n += 1
            candidate = f"{base}-{n}"
        return candidate

    def _manifest(
        self,
        *,
        model_id,
        name,
        role,
        catalog_id,
        source,
        file: Path,
        sha256,
        sha_verified,
        info: GGUFInfo,
        license,
        context_length,
        embedding_dimensions,
    ) -> dict:
        return {
            "id": model_id,
            "name": name,
            "role": role,
            "catalog_id": catalog_id,
            "source": source,
            "file": file.name,
            "rel_path": self.paths.relative_to_data(file),
            "size_bytes": file.stat().st_size,
            "sha256": sha256,
            "sha256_verified": bool(sha_verified),
            "architecture": info.architecture,
            "quantization": info.quantization,
            "context_length": int(context_length or info.context_length or 4096),
            "gguf_context_length": info.context_length,
            "embedding_dimensions": embedding_dimensions,
            "license": license,
            "metadata": {
                **{k: v for k, v in info.metadata.items() if isinstance(v, (str, int, float, bool))},
                "has_chat_template": info.has_chat_template,
                "chat_template_excerpt": (info.chat_template or "")[:4000],
            },
            "state": "ready",
            "created_at": utcnow(),
            "runtime_build": "b12032",
        }

    @staticmethod
    def _write_manifest(target_dir: Path, manifest: dict) -> None:
        tmp = target_dir / (MANIFEST + ".tmp")
        tmp.write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding="utf-8")
        tmp.replace(target_dir / MANIFEST)

    def _upsert(self, m: dict) -> None:
        self.db.execute(
            """
            INSERT INTO models(id, name, role, catalog_id, source, rel_path, size_bytes, sha256, sha256_verified,
                               architecture, quantization, context_length, embedding_dimensions, license_json,
                               metadata_json, state, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name, role = excluded.role, catalog_id = excluded.catalog_id,
                source = excluded.source, rel_path = excluded.rel_path, size_bytes = excluded.size_bytes,
                sha256 = excluded.sha256, sha256_verified = excluded.sha256_verified,
                architecture = excluded.architecture, quantization = excluded.quantization,
                context_length = excluded.context_length, embedding_dimensions = excluded.embedding_dimensions,
                license_json = excluded.license_json, metadata_json = excluded.metadata_json, state = excluded.state
            """,
            (
                m["id"],
                m["name"],
                m["role"],
                m.get("catalog_id"),
                m["source"],
                m["rel_path"],
                int(m["size_bytes"]),
                m.get("sha256"),
                1 if m.get("sha256_verified") else 0,
                m.get("architecture"),
                m.get("quantization"),
                m.get("context_length"),
                m.get("embedding_dimensions"),
                json.dumps(m.get("license")) if m.get("license") is not None else None,
                json.dumps(m.get("metadata") or {}),
                m.get("state", "ready"),
                m.get("created_at") or utcnow(),
            ),
        )

    def _row_to_api(self, row, active: dict[str, Optional[str]]) -> dict:
        return {
            "id": row["id"],
            "name": row["name"],
            "role": row["role"],
            "catalog_id": row["catalog_id"],
            "source": row["source"],
            "path": str(self.paths.from_relative(row["rel_path"])),
            "rel_path": row["rel_path"],
            "size_bytes": row["size_bytes"],
            "sha256": row["sha256"],
            "sha256_verified": bool(row["sha256_verified"]),
            "architecture": row["architecture"],
            "quantization": row["quantization"],
            "context_length": row["context_length"],
            "embedding_dimensions": row["embedding_dimensions"],
            "license": json.loads(row["license_json"]) if row["license_json"] else None,
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "state": row["state"],
            "active": active.get(row["role"]) == row["id"],
            "created_at": row["created_at"],
        }


def _slug(text: str) -> str:
    s = re.sub(r"[^a-z0-9._-]+", "-", text.lower()).strip("-.")
    return s[:60] if _ID_RE.match(s or "x") else f"model-{uuid.uuid4().hex[:8]}"
