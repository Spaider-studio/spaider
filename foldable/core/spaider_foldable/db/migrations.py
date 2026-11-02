"""Versioned, forward-only schema migrations.

Each migration runs inside its own transaction and is recorded in
``schema_migrations``. ``PRAGMA user_version`` mirrors the latest version so a
foreign tool can read it without knowing the table.
"""

from __future__ import annotations

import sqlite3
from importlib import resources

CURRENT_VERSION = 1

MIGRATIONS: list[tuple[int, str, str]] = [
    (1, "initial schema", "schema_v1.sql"),
]


def _sql(name: str) -> str:
    pkg = resources.files("spaider_foldable.db")
    return (pkg / name).read_text(encoding="utf-8")


def _split_statements(sql: str) -> list[str]:
    """Split a schema file into statements. Trigger bodies contain ';' inside
    BEGIN ... END, so a statement only ends at ';' outside a trigger body."""
    statements: list[str] = []
    buf: list[str] = []
    depth = 0
    for line in sql.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("--"):
            continue
        buf.append(line)
        upper = stripped.upper()
        if upper.endswith("BEGIN"):
            depth += 1
            continue
        if upper == "END;":
            depth -= 1
            statements.append("\n".join(buf))
            buf = []
            continue
        if depth == 0 and stripped.endswith(";"):
            statements.append("\n".join(buf))
            buf = []
    if buf:
        statements.append("\n".join(buf))
    return [s for s in statements if s.strip()]


def run_schema(conn: sqlite3.Connection, sql: str) -> None:
    # executescript() would issue an implicit COMMIT; we are inside an explicit
    # transaction, so run the statements one by one.
    for stmt in _split_statements(sql):
        conn.execute(stmt)


def applied_versions(conn: sqlite3.Connection) -> set[int]:
    conn.execute(
        "CREATE TABLE IF NOT EXISTS schema_migrations ("
        "version INTEGER PRIMARY KEY, name TEXT NOT NULL, applied_at TEXT NOT NULL)"
    )
    return {row[0] for row in conn.execute("SELECT version FROM schema_migrations")}


def migrate(conn: sqlite3.Connection) -> list[int]:
    """Apply pending migrations. Returns the versions applied in this call."""
    done = applied_versions(conn)
    applied: list[int] = []
    for version, name, filename in MIGRATIONS:
        if version in done:
            continue
        sql = _sql(filename)
        try:
            conn.execute("BEGIN IMMEDIATE")
            run_schema(conn, sql)
            conn.execute(
                "INSERT INTO schema_migrations(version, name, applied_at) "
                "VALUES (?, ?, strftime('%Y-%m-%dT%H:%M:%fZ','now'))",
                (version, name),
            )
            conn.execute(f"PRAGMA user_version = {int(version)}")
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise
        applied.append(version)
    return applied


def schema_version(conn: sqlite3.Connection) -> int:
    versions = applied_versions(conn)
    return max(versions) if versions else 0
