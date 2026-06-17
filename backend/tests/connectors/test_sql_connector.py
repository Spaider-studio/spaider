"""
Unit tests for ``SQLConnector`` ([](/Spaider-studio/spaider/issues/54)).

Strategy
--------
Tests run against an in-memory SQLite DB via ``aiosqlite``. No LLM calls,
no Neo4j writes, no real network — the connector's pagination + cursor
behaviour is exercised in isolation.

Coverage:

  1. Full sync — empty RunState, 5 rows, all 5 records yielded with
     ascending cursors; record fields populated correctly.
  2. Incremental sync — RunState seeded with cursor=5, 5 new rows added
     (id 6-10), only those 5 yielded.
  3. Empty table — yields nothing, no error.
  4. ``initial_cursor`` kwarg overrides RunState's stored cursor.
  5. Bad cursor column raises a clear ``ValueError`` (input validation).
"""
from __future__ import annotations

from pathlib import Path

import aiosqlite
import pytest

from app.connectors import RunState
from app.connectors.sql_connector import SQLConnector, _CURSOR_KEY


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _dsn(path: str) -> str:
    return f"sqlite+aiosqlite:///{path}"


async def _seed(path: str, rows: list[tuple[int, str, str]]) -> None:
    async with aiosqlite.connect(path) as db:
        await db.execute(
            "CREATE TABLE IF NOT EXISTS products "
            "(id INTEGER PRIMARY KEY, name TEXT NOT NULL, description TEXT)"
        )
        await db.executemany(
            "INSERT INTO products VALUES (?, ?, ?)", rows,
        )
        await db.commit()


@pytest.fixture
async def seeded_db(tmp_path: Path) -> str:
    path = str(tmp_path / "products.db")
    await _seed(path, [(i, f"Product {i}", f"Description for product {i}") for i in range(1, 6)])
    return path


# ---------------------------------------------------------------------------
# 1. Full sync
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_full_sync_yields_every_row(seeded_db: str):
    connector = SQLConnector()
    run_state = RunState(connector_id="sql")

    records = []
    async for record in connector.run(
        agent_id="agent-x",
        run_state=run_state,
        dsn=_dsn(seeded_db),
        query="SELECT * FROM products",
        cursor_column="id",
        content_columns=["name", "description"],
        title_column="name",
        id_column="id",
        batch_size=10,
    ):
        records.append(record)

    assert len(records) == 5
    # Each yielded record carries the framework's connector + agent IDs
    assert {r.connector_id for r in records} == {"sql"}
    assert {r.agent_id for r in records} == {"agent-x"}
    # source_uri encodes the cursor for traceability
    assert records[0].source_uri == "sql://sql/id=1"
    assert records[-1].source_uri == "sql://sql/id=5"
    # Title comes from title_column
    assert records[0].hints["title"] == "Product 1"
    # Content concatenates content_columns in order
    assert "Product 1" in records[0].text
    assert "Description for product 1" in records[0].text
    # RunState cursor is at the last seen value
    assert run_state.source_states[_CURSOR_KEY]["cursor"] == 5


# ---------------------------------------------------------------------------
# 2. Incremental sync — RunState's stored cursor is honoured
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_incremental_sync_resumes_from_runstate_cursor(seeded_db: str):
    # Add 5 more rows so total table has ids 1..10
    await _seed(
        seeded_db,
        [(i, f"Product {i}", f"Description for product {i}") for i in range(6, 11)],
    )

    run_state = RunState(
        connector_id="sql",
        source_states={_CURSOR_KEY: {"cursor": 5}},
    )

    connector = SQLConnector()
    records = []
    async for record in connector.run(
        agent_id="agent-x",
        run_state=run_state,
        dsn=_dsn(seeded_db),
        query="SELECT * FROM products",
        cursor_column="id",
        content_columns=["name", "description"],
        batch_size=10,
    ):
        records.append(record)

    assert len(records) == 5
    cursors = [r.metadata["cursor_value"] for r in records]
    assert cursors == [6, 7, 8, 9, 10]
    assert run_state.source_states[_CURSOR_KEY]["cursor"] == 10


# ---------------------------------------------------------------------------
# 3. Empty table
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_empty_table_yields_nothing(tmp_path: Path):
    db_path = str(tmp_path / "empty.db")
    async with aiosqlite.connect(db_path) as db:
        await db.execute(
            "CREATE TABLE products (id INTEGER PRIMARY KEY, name TEXT, description TEXT)"
        )
        await db.commit()

    connector = SQLConnector()
    run_state = RunState(connector_id="sql")
    records = []
    async for record in connector.run(
        agent_id="agent-x",
        run_state=run_state,
        dsn=_dsn(db_path),
        query="SELECT * FROM products",
        cursor_column="id",
        batch_size=10,
    ):
        records.append(record)

    assert records == []
    # No cursor recorded — there were no rows
    assert _CURSOR_KEY not in run_state.source_states


# ---------------------------------------------------------------------------
# 4. initial_cursor overrides the RunState's stored cursor
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_initial_cursor_kwarg_overrides_runstate(seeded_db: str):
    # RunState says cursor=2, but we pass initial_cursor=4 explicitly.
    run_state = RunState(
        connector_id="sql",
        source_states={_CURSOR_KEY: {"cursor": 2}},
    )

    connector = SQLConnector()
    records = []
    async for record in connector.run(
        agent_id="agent-x",
        run_state=run_state,
        dsn=_dsn(seeded_db),
        query="SELECT * FROM products",
        cursor_column="id",
        content_columns=["name", "description"],
        initial_cursor=4,
        batch_size=10,
    ):
        records.append(record)

    cursors = [r.metadata["cursor_value"] for r in records]
    assert cursors == [5]


# ---------------------------------------------------------------------------
# 5. Input validation: cursor_column rejects garbage
# ---------------------------------------------------------------------------


@pytest.mark.smoke
@pytest.mark.asyncio
async def test_invalid_cursor_column_raises(seeded_db: str):
    connector = SQLConnector()
    with pytest.raises(ValueError):
        async for _ in connector.run(
            agent_id="agent-x",
            run_state=RunState(connector_id="sql"),
            dsn=_dsn(seeded_db),
            query="SELECT * FROM products",
            cursor_column="id; DROP TABLE products;--",
        ):
            pass
