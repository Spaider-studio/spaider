"""Profiles (T8/T12 parts): legacy migration into the first profile (resumable),
registry operations, shared vs. profile-scoped paths, same-process lock."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from spaider_foldable.lock import InstanceLock
from spaider_foldable.paths import PortablePaths
from spaider_foldable.profiles import ProfileError, ProfileRegistry


def _legacy(root: Path) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "spaider.db").write_bytes(b"sqlite-bytes")
    (root / "spaider.db-wal").write_bytes(b"wal")
    (root / "models" / "m1").mkdir(parents=True)
    (root / "models" / "m1" / "w.gguf").write_bytes(b"x")
    (root / "imports").mkdir()
    (root / "logs").mkdir()
    (root / "logs" / "core.log").write_text("log", encoding="utf-8")
    (root / "host").mkdir()
    (root / "host" / "credentials.json").write_text("{}", encoding="utf-8")


def test_legacy_layout_moves_into_first_profile(tmp_path):
    root = tmp_path / "data"
    _legacy(root)
    reg = ProfileRegistry(root)
    d = reg.ensure_initialized()
    pid = d["active_id"]
    prof = root / "profiles" / pid
    assert (prof / "spaider.db").read_bytes() == b"sqlite-bytes" and (prof / "spaider.db-wal").exists()
    assert (prof / "models" / "m1" / "w.gguf").exists() and (prof / "imports").is_dir()
    # shared things stay at the root
    assert (root / "logs" / "core.log").exists() and (root / "host" / "credentials.json").exists()
    assert not (root / "spaider.db").exists() and not (root / "models").exists()
    assert reg.list()[0]["name"] == "Standard" and reg.list()[0]["db_bytes"] == len(b"sqlite-bytes")
    assert not (root / "profiles.migrating.json").exists()
    # second start: no change
    assert reg.ensure_initialized()["active_id"] == pid


def test_migration_resumes_from_journal(tmp_path):
    root = tmp_path / "data"
    _legacy(root)
    pid = "11111111-1111-1111-1111-111111111111"
    # simulate a crash after moving the db but before models/imports
    (root / "profiles" / pid).mkdir(parents=True)
    os.replace(root / "spaider.db", root / "profiles" / pid / "spaider.db")
    (root / "profiles.migrating.json").write_text(json.dumps({"profile_id": pid, "items": ["spaider.db", "spaider.db-wal", "models", "imports"]}), encoding="utf-8")
    reg = ProfileRegistry(root)
    d = reg.ensure_initialized()
    assert (root / "profiles" / pid / "models" / "m1" / "w.gguf").exists()
    assert not (root / "profiles.migrating.json").exists()
    # the resumed plan did not create the registry (crash before writing it): a fresh profile is created
    # for the remaining legacy state only if any is left; here nothing is left so the registry has one profile
    assert len(d["profiles"]) == 1


def test_registry_create_rename_delete_rules(tmp_path):
    root = tmp_path / "data"
    root.mkdir()
    reg = ProfileRegistry(root)
    d = reg.ensure_initialized()
    first = d["active_id"]
    b = reg.create("Zweites Profil")
    assert (root / "profiles" / b["id"]).is_dir() and reg.active_id() == first
    reg.rename(b["id"], "B")
    assert reg.get(b["id"])["name"] == "B"
    with pytest.raises(ProfileError) as exc:
        reg.delete(first, active=first)
    assert exc.value.code == "active"
    reg.set_active(b["id"])
    reg.delete(first, active=b["id"])
    assert reg.get(first) is None and not (root / "profiles" / first).exists()
    with pytest.raises(ProfileError):
        reg.set_active("nope")


def test_profile_paths_share_host_level_locations(tmp_path):
    base = PortablePaths.from_app_root(tmp_path / "App")
    prof = base.for_profile("abc")
    assert prof.data_dir == base.data_dir / "profiles" / "abc" and prof.profile_id == "abc"
    assert prof.lock_path == base.lock_path and prof.logs_dir == base.logs_dir and prof.host_dir == base.host_dir and prof.electron_dir == base.electron_dir
    assert prof.db_path.parent == prof.data_dir and prof.models_dir.parent == prof.data_dir and prof.vault_path.parent == prof.data_dir
    prof.ensure()
    assert prof.models_dir.is_dir() and base.host_dir.is_dir()
    assert prof.is_inside_data(prof.models_dir / "x") and not prof.is_inside_data(base.data_dir / "profiles" / "other")


def test_shared_lock_object_acquires_once(tmp_path):
    from spaider_foldable.lock import AlreadyRunningError

    p = tmp_path / "instance.lock"
    shared = InstanceLock(p)
    shared.acquire()
    shared.acquire()  # idempotent for the same object (profile switch keeps the lock)
    assert shared.held and p.exists()
    with pytest.raises(AlreadyRunningError):
        InstanceLock(p).acquire()  # a second start (other object/process) is still refused
    shared.release()
