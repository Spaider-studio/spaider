from __future__ import annotations

import json
import os
import shutil
import sqlite3
from pathlib import Path

import pytest

from spaider_foldable.db import Database, DatabaseCorruptError
from spaider_foldable.db.migrations import CURRENT_VERSION, schema_version
from spaider_foldable.lock import AlreadyRunningError, InstanceLock
from spaider_foldable.paths import PortablePathError, PortablePaths


def test_paths_derive_only_from_app_root(app_root):
    p = PortablePaths.from_app_root(app_root)
    assert p.data_dir == app_root / "data"
    assert p.models_dir.parent == p.data_dir
    assert " " in str(p.data_dir) and "Ü" in str(p.data_dir)
    with pytest.raises(PortablePathError):
        PortablePaths.from_app_root("relative/dir")


def test_paths_traversal_guard(paths):
    assert paths.is_inside_data(paths.models_dir / "x" / "y.gguf")
    assert not paths.is_inside_data(paths.data_dir / ".." / "outside.txt")
    assert not paths.is_inside_data(Path(os.environ.get("TEMP", "/tmp")))
    rel = paths.relative_to_data(paths.imports_dir / "doc" / "a b ü.pdf")
    assert rel == "imports/doc/a b ü.pdf"
    assert paths.from_relative(rel) == (paths.imports_dir / "doc" / "a b ü.pdf").resolve()
    with pytest.raises(PortablePathError):
        paths.from_relative("../../evil")


def test_relative_paths_survive_move(app_root, tmp_path):
    p1 = PortablePaths.from_app_root(app_root)
    p1.ensure()
    f = p1.models_dir / "m" / "model.gguf"
    f.parent.mkdir(parents=True)
    f.write_bytes(b"x")
    rel = p1.relative_to_data(f)
    new_root = tmp_path / "Anderer Ort é" / "SpAIder"
    new_root.parent.mkdir()
    shutil.move(str(app_root), str(new_root))
    p2 = PortablePaths.from_app_root(new_root)
    assert p2.from_relative(rel).read_bytes() == b"x"


def test_db_migrates_and_reopens(paths):
    db = Database(paths.db_path)
    db.open()
    assert schema_version(db.conn) == CURRENT_VERSION
    assert db.scalar("PRAGMA journal_mode") == "wal"
    assert db.scalar("PRAGMA user_version") == CURRENT_VERSION
    db.execute("INSERT INTO notes(id, text, created_at, updated_at) VALUES ('n1', 'Müller wohnt in Köln', 'x', 'x')")
    db.close()
    # WAL checkpointed on close: no leftover -wal content needed to read the row
    db2 = Database(paths.db_path)
    db2.open()
    assert db2.scalar("SELECT count(*) FROM notes") == 1
    assert schema_version(db2.conn) == CURRENT_VERSION  # idempotent
    assert db2.query("SELECT text FROM notes_fts WHERE notes_fts MATCH 'koln'")[0][0] == "Müller wohnt in Köln"
    db2.close()


def test_db_transaction_rollback(db):
    with pytest.raises(RuntimeError):
        with db.transaction() as conn:
            conn.execute("INSERT INTO notes(id, text, created_at, updated_at) VALUES ('n1', 't', 'x', 'x')")
            raise RuntimeError("boom")
    assert db.scalar("SELECT count(*) FROM notes") == 0


def test_db_corruption_detected(paths):
    paths.db_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 200)
    db = Database(paths.db_path)
    with pytest.raises((DatabaseCorruptError, sqlite3.DatabaseError)):
        db.open()


def test_instance_lock_second_start_refused(paths):
    lock = InstanceLock(paths.lock_path)
    lock.acquire()
    second = InstanceLock(paths.lock_path)
    with pytest.raises(AlreadyRunningError):
        second.acquire()
    lock.release()
    second.acquire()
    second.release()


def test_instance_lock_stale_after_crash(paths):
    paths.lock_path.write_text(
        json.dumps({"pid": 999999, "hostname": "other-host", "started_at": "x"}), encoding="utf-8"
    )
    lock = InstanceLock(paths.lock_path)
    lock.acquire()  # stale lock from another host is taken over
    assert json.loads(paths.lock_path.read_text())["pid"] == os.getpid()
    lock.release()
    assert not paths.lock_path.exists()
