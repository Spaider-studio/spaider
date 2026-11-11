"""Portable path resolution.

Every persistent location the application controls is derived from ONE value:
the application root directory (the folder that holds ``SpAIder.exe``). It is
passed explicitly by the launcher (``--app-root``). It is never derived from the
current working directory, a fixed drive letter, or the user profile.

Layout (all inside ``<app_root>/data``)::

    data/
      spaider.db (+ -wal, -shm)   SQLite database
      instance.lock               single-instance lock (JSON)
      models/<model_id>/          GGUF files + manifest.json
      downloads/<job>/            partial downloads (resumable), removed on success
      imports/<document_id>/      copies of imported originals
      logs/                       core + runtime logs
      cache/                      model-bound caches that may be discarded
      tmp/                        scratch space, cleaned at start
      electron/                   Electron userData/session/cache (set by the launcher)
"""

from __future__ import annotations

import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Optional


class PortablePathError(ValueError):
    """Raised when the application root is unusable."""


@dataclass(frozen=True)
class PortablePaths:
    app_root: Path
    data_dir: Path
    root_data_dir: Optional[Path] = None  # host-level ``data/`` when data_dir is a profile folder
    profile_id: str = ""

    # ---- derived -------------------------------------------------------
    @property
    def shared_dir(self) -> Path:
        """Locations shared by all profiles (lock, logs, electron, host-bound state)."""
        return self.root_data_dir or self.data_dir

    def for_profile(self, profile_id: str) -> "PortablePaths":
        root = self.root_data_dir or self.data_dir
        return PortablePaths(app_root=self.app_root, data_dir=root / "profiles" / profile_id, root_data_dir=root, profile_id=profile_id)

    @property
    def db_path(self) -> Path:
        return self.data_dir / "spaider.db"

    @property
    def lock_path(self) -> Path:
        return self.shared_dir / "instance.lock"

    @property
    def models_dir(self) -> Path:
        return self.data_dir / "models"

    @property
    def downloads_dir(self) -> Path:
        return self.data_dir / "downloads"

    @property
    def imports_dir(self) -> Path:
        return self.data_dir / "imports"

    @property
    def logs_dir(self) -> Path:
        return self.shared_dir / "logs"

    @property
    def cache_dir(self) -> Path:
        return self.data_dir / "cache"

    @property
    def tmp_dir(self) -> Path:
        return self.data_dir / "tmp"

    @property
    def electron_dir(self) -> Path:
        return self.shared_dir / "electron"

    @property
    def host_dir(self) -> Path:
        """Machine-specific, non-portable state (host-bound credentials, connector setup)."""
        return self.shared_dir / "host"

    @property
    def vault_path(self) -> Path:
        return self.data_dir / "vault.json"

    @property
    def connectors_dir(self) -> Path:
        return self.shared_dir / "connectors"

    # ---- construction ---------------------------------------------------
    @classmethod
    def from_app_root(cls, app_root: str | os.PathLike[str]) -> "PortablePaths":
        root = Path(app_root)
        if not root.is_absolute():
            raise PortablePathError(f"app root must be an absolute path, got {root!s}")
        root = root.resolve(strict=False)
        if root.exists() and not root.is_dir():
            raise PortablePathError(f"app root is not a directory: {root!s}")
        return cls(app_root=root, data_dir=root / "data")

    def ensure(self) -> None:
        for d in (
            self.data_dir,
            self.models_dir,
            self.downloads_dir,
            self.imports_dir,
            self.logs_dir,
            self.cache_dir,
            self.tmp_dir,
            self.host_dir,
            self.connectors_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
        # tmp is scratch only: wipe leftovers from a previous crashed run.
        for child in self.tmp_dir.iterdir():
            try:
                if child.is_dir():
                    shutil.rmtree(child, ignore_errors=True)
                else:
                    child.unlink(missing_ok=True)
            except OSError:
                pass

    # ---- safety -----------------------------------------------------------
    def is_inside_data(self, candidate: str | os.PathLike[str]) -> bool:
        """True when ``candidate`` resolves to a location inside ``data_dir``.

        Used as the traversal guard for every path that originates from user or
        model input (document ids, model ids, file names).
        """
        try:
            resolved = Path(candidate).resolve(strict=False)
        except (OSError, RuntimeError):
            return False
        try:
            resolved.relative_to(self.data_dir.resolve(strict=False))
            return True
        except ValueError:
            return False

    def relative_to_data(self, path: str | os.PathLike[str]) -> str:
        """Store paths relative to ``data_dir`` (forward slashes) so the data
        directory stays valid after moving to another drive letter or host."""
        rel = Path(path).resolve(strict=False).relative_to(self.data_dir.resolve(strict=False))
        return rel.as_posix()

    def from_relative(self, rel: str) -> Path:
        p = (self.data_dir / Path(rel)).resolve(strict=False)
        if not self.is_inside_data(p):
            raise PortablePathError(f"path escapes the data directory: {rel!r}")
        return p
