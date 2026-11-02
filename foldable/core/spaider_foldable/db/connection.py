"""SQLite connection wrapper: WAL journal, busy timeout, integrity check on
open, a single process-wide lock that serialises all access, explicit
transactions, and an orderly close that checkpoints the WAL.

Why WAL + one lock
------------------
The portable app has exactly one writer (this process). WAL gives crash safety
(a torn write never corrupts the main file; recovery happens on next open) and
lets the file be copied/moved safely after ``close()`` has checkpointed it. The
RLock keeps the sqlite3 connection usable from FastAPI worker threads without
"database is locked" surprises; heavy work is pushed to a thread with
``asyncio.to_thread`` by callers.
"""

from __future__ import annotations

import contextlib
import logging
import sqlite3
import threading
from pathlib import Path
from typing import Any, Iterable, Iterator

from . import migrations

logger = logging.getLogger(__name__)


class DatabaseCorruptError(RuntimeError):
    pass


class Database:
    def __init__(self, path: Path):
        self.path = Path(path)
        self._lock = threading.RLock()
        self._conn: sqlite3.Connection | None = None
        self._in_tx = 0

    # ---- lifecycle ----------------------------------------------------
    def open(self) -> None:
        with self._lock:
            if self._conn is not None:
                return
            self.path.parent.mkdir(parents=True, exist_ok=True)
            conn = sqlite3.connect(str(self.path), check_same_thread=False, isolation_level=None, timeout=5.0)
            conn.row_factory = sqlite3.Row
            conn.execute("PRAGMA journal_mode = WAL")
            conn.execute("PRAGMA synchronous = NORMAL")
            conn.execute("PRAGMA foreign_keys = ON")
            conn.execute("PRAGMA busy_timeout = 5000")
            conn.execute("PRAGMA temp_store = MEMORY")
            check = conn.execute("PRAGMA quick_check").fetchone()[0]
            if check != "ok":
                conn.close()
                raise DatabaseCorruptError(
                    f"Die Datenbank {self.path} ist beschädigt (quick_check: {check}). "
                    "Bitte eine Sicherung einspielen; die Datei wird nicht automatisch ersetzt."
                )
            self._conn = conn
            applied = migrations.migrate(conn)
            if applied:
                logger.info("Applied schema migrations: %s", applied)

    @property
    def is_open(self) -> bool:
        return self._conn is not None

    def close(self) -> None:
        with self._lock:
            if self._conn is None:
                return
            try:
                self._conn.execute("PRAGMA wal_checkpoint(TRUNCATE)")
            except sqlite3.Error as exc:
                logger.warning("WAL checkpoint on close failed: %s", exc)
            self._conn.close()
            self._conn = None

    # ---- access ---------------------------------------------------------
    @property
    def conn(self) -> sqlite3.Connection:
        if self._conn is None:
            raise RuntimeError("database is closed")
        return self._conn

    @contextlib.contextmanager
    def locked(self) -> Iterator[sqlite3.Connection]:
        with self._lock:
            yield self.conn

    @contextlib.contextmanager
    def transaction(self) -> Iterator[sqlite3.Connection]:
        """Nested-safe explicit transaction (outermost BEGIN IMMEDIATE)."""
        with self._lock:
            conn = self.conn
            outermost = self._in_tx == 0
            if outermost:
                conn.execute("BEGIN IMMEDIATE")
            self._in_tx += 1
            try:
                yield conn
            except BaseException:
                self._in_tx -= 1
                if outermost:
                    conn.execute("ROLLBACK")
                raise
            else:
                self._in_tx -= 1
                if outermost:
                    conn.execute("COMMIT")

    def execute(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Cursor:
        with self._lock:
            return self.conn.execute(sql, tuple(params))

    def executemany(self, sql: str, rows: Iterable[Iterable[Any]]) -> None:
        with self._lock:
            self.conn.executemany(sql, [tuple(r) for r in rows])

    def query(self, sql: str, params: Iterable[Any] = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchall()

    def query_one(self, sql: str, params: Iterable[Any] = ()) -> sqlite3.Row | None:
        with self._lock:
            return self.conn.execute(sql, tuple(params)).fetchone()

    def scalar(self, sql: str, params: Iterable[Any] = ()) -> Any:
        row = self.query_one(sql, params)
        return None if row is None else row[0]

    def checkpoint(self) -> None:
        with self._lock:
            self.conn.execute("PRAGMA wal_checkpoint(PASSIVE)")

    def file_size_bytes(self) -> int:
        total = 0
        for suffix in ("", "-wal", "-shm"):
            p = Path(str(self.path) + suffix)
            if p.exists():
                total += p.stat().st_size
        return total
