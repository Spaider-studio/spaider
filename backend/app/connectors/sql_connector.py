"""
SQL Connector — streams rows from any SQLAlchemy-compatible database into
SpAIder's knowledge graph.

Implements [](/Spaider-studio/spaider/issues/54). Subclass of the canonical
``app.connectors.BaseConnector`` — same contract used by ``URLConnector``,
``UploadConnector``, and ``MCPConnector``. Registered in the global
``ConnectorRegistry`` from ``backend/app/api/v1/ingest.py``.

Supported dialects (out of the box, aarch64-linux):
  - ``postgresql+asyncpg://...``
  - ``sqlite+aiosqlite:///path/to/db``
  - ``mysql+aiomysql://...`` (requires the optional ``aiomysql`` dep)

DSN handling
------------
For v1 the DSN is accepted as a plaintext kwarg on ``run()``. Persisted
configs and the encrypted-DSN path through ``SecretsService`` are tracked
as a follow-up — keeps this PR focused on the connector contract.

Incremental sync
----------------
Per-stream cursor stored under a fixed key in ``run_state.source_states``:

    run_state.source_states["__sql_cursor__"] = {"cursor": <last_seen_value>}

The connector reads this on entry and writes the most recent emitted row's
cursor on every yield. The caller persists ``run_state`` after the run.

Subquery wrapping
-----------------
The user's ``query`` may end with ``GROUP BY``, ``HAVING``, ``ORDER BY``,
or ``LIMIT`` — appending a trailing ``WHERE`` would be syntactically
invalid against any of those. Every query is therefore wrapped:

    SELECT * FROM ({user_query}) AS _spaider_subq
     WHERE _spaider_subq.{cursor_col} > :cursor
     ORDER BY _spaider_subq.{cursor_col} ASC
     LIMIT :batch_size

DLQ
---
Per-row exceptions (parse error, missing column, etc.) are logged and the
loop continues — a single bad row never aborts the stream. Failures
serious enough to raise out of the engine (auth, connection drop) propagate
to the caller.
"""
from __future__ import annotations

import hashlib
import logging
import re
from typing import Any, AsyncGenerator, Optional

from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine

from app.connectors import BaseConnector, ConnectorRecord, RunState

logger = logging.getLogger(__name__)


# Fixed key under run_state.source_states for the SQL connector's cursor.
# A SQL connector run always corresponds to *one* logical stream (the
# user-supplied query), so we don't need per-row keys.
_CURSOR_KEY = "__sql_cursor__"

# Identifier whitelist for cursor_column / id_column / title_column to defend
# against SQL injection through column names that we cannot bind as parameters.
_IDENT_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")


def _validate_identifier(name: str, field: str) -> str:
    if not isinstance(name, str) or not _IDENT_RE.match(name):
        raise ValueError(
            f"{field} must match /^[A-Za-z_][A-Za-z0-9_]*$/, got {name!r}"
        )
    return name


class SQLConnector(BaseConnector):
    """Streams rows from any SQLAlchemy DSN as ``ConnectorRecord``s."""

    connector_id = "sql"

    async def run(  # type: ignore[override]
        self,
        agent_id: str,
        run_state: RunState,
        *,
        dsn: str,
        query: str,
        cursor_column: str = "id",
        content_columns: Optional[list[str]] = None,
        title_column: Optional[str] = None,
        id_column: Optional[str] = None,
        batch_size: int = 1000,
        initial_cursor: Optional[Any] = None,
        source_label: str = "sql",
    ) -> AsyncGenerator[ConnectorRecord, None]:
        """
        Stream rows from the configured query.

        Parameters
        ----------
        agent_id : str
            Owning agent namespace — stamped on every yielded record.
        run_state : RunState
            Cursor read from / written to ``run_state.source_states[__sql_cursor__]``.
        dsn : str
            SQLAlchemy DSN (e.g. ``postgresql+asyncpg://user:pass@h/db``).
        query : str
            Arbitrary ``SELECT`` / ``WITH`` query. Wrapped before pagination.
        cursor_column : str
            Monotonically-increasing column the connector pages by.
        content_columns : list[str] | None
            Columns whose values are concatenated (newline-separated) to form
            ``record.text``. Empty list / None → use all non-cursor columns.
        title_column, id_column :
            Optional column names. Default to ``cursor_column`` for id,
            ``str(cursor_value)`` for title.
        batch_size : int
            Server-side ``yield_per`` page size.
        initial_cursor : Any
            Resume value. ``None`` reads ``run_state.source_states[__sql_cursor__]``;
            absent there → start from before the lowest possible value.
        source_label : str
            Tag included in record metadata for provenance.
        """
        if not query.strip().upper().startswith(("SELECT", "WITH")):
            raise ValueError("query must start with SELECT or WITH")
        if ";" in query.replace("\\;", ""):
            raise ValueError("query must not contain ';' (use a single statement)")

        cursor_column = _validate_identifier(cursor_column, "cursor_column")
        if title_column is not None:
            _validate_identifier(title_column, "title_column")
        if id_column is not None:
            _validate_identifier(id_column, "id_column")

        # Resolve initial cursor: explicit kwarg > stored state > sentinel min.
        cursor_val: Any = initial_cursor
        if cursor_val is None:
            stored = run_state.source_states.get(_CURSOR_KEY)
            if stored is not None:
                cursor_val = stored.get("cursor")

        paginated_sql = (
            f"SELECT * FROM ({query}) AS _spaider_subq "
            f"WHERE _spaider_subq.{cursor_column} > :cursor "
            f"ORDER BY _spaider_subq.{cursor_column} ASC "
            f"LIMIT :batch_size"
        )

        logger.info(
            "sql_connector: start  agent_id=%s  cursor_column=%s  "
            "batch_size=%d  initial_cursor=%r",
            agent_id, cursor_column, batch_size, cursor_val,
        )

        engine = create_async_engine(dsn, echo=False, pool_pre_ping=True)
        rows_yielded = 0
        try:
            async with engine.begin() as conn:
                while True:
                    bind_cursor = (
                        cursor_val
                        if cursor_val is not None
                        else _sentinel_min_for(cursor_column)
                    )
                    rows_in_batch = 0

                    result = await conn.stream(
                        text(paginated_sql),
                        {"cursor": bind_cursor, "batch_size": batch_size},
                        execution_options={"yield_per": batch_size},
                    )
                    async for row in result:
                        rows_in_batch += 1
                        try:
                            record = _row_to_record(
                                row,
                                agent_id=agent_id,
                                cursor_column=cursor_column,
                                content_columns=content_columns,
                                title_column=title_column,
                                id_column=id_column,
                                source_label=source_label,
                            )
                        except Exception as exc:
                            logger.warning(
                                "sql_connector: row parse error agent=%s row=%r: %s",
                                agent_id,
                                _row_to_dict(row),
                                exc,
                            )
                            continue  # DLQ: skip bad row

                        cursor_val = _extract_cursor_value(row, cursor_column)
                        run_state.source_states[_CURSOR_KEY] = {"cursor": cursor_val}
                        rows_yielded += 1
                        yield record

                    if rows_in_batch < batch_size:
                        break
        finally:
            await engine.dispose()
            logger.info(
                "sql_connector: complete  agent_id=%s  rows_yielded=%d  last_cursor=%r",
                agent_id, rows_yielded, cursor_val,
            )


# ---------------------------------------------------------------------------
# Helpers (module-level so tests can also exercise them in isolation)
# ---------------------------------------------------------------------------


def _row_to_dict(row: Any) -> dict[str, Any]:
    """Map a SQLAlchemy Row to a dict, tolerating missing ``_mapping``."""
    if hasattr(row, "_mapping"):
        return dict(row._mapping)
    return dict(row) if isinstance(row, dict) else {"value": str(row)}


def _extract_cursor_value(row: Any, cursor_column: str) -> Any:
    mapping = _row_to_dict(row)
    if cursor_column not in mapping:
        raise KeyError(f"cursor_column {cursor_column!r} not in row {list(mapping)}")
    return mapping[cursor_column]


def _row_to_record(
    row: Any,
    *,
    agent_id: str,
    cursor_column: str,
    content_columns: Optional[list[str]],
    title_column: Optional[str],
    id_column: Optional[str],
    source_label: str,
) -> ConnectorRecord:
    mapping = _row_to_dict(row)

    if cursor_column not in mapping:
        raise KeyError(f"cursor_column {cursor_column!r} not in row {list(mapping)}")

    cursor_value = mapping[cursor_column]

    # Choose ID
    if id_column and id_column in mapping:
        external_raw = mapping[id_column]
    else:
        external_raw = cursor_value
    external_id = hashlib.sha256(str(external_raw).encode("utf-8")).hexdigest()[:32]

    # Choose title
    if title_column and title_column in mapping:
        title = str(mapping[title_column])
    else:
        title = str(cursor_value)

    # Choose content columns
    if content_columns:
        chunks = [
            str(mapping[c]) for c in content_columns if c in mapping and mapping[c] is not None
        ]
    else:
        chunks = [
            str(mapping[c])
            for c in mapping
            if c != cursor_column and mapping[c] is not None
        ]
    content = "\n".join(chunks)

    source_uri = f"sql://{source_label}/{cursor_column}={cursor_value}"

    return ConnectorRecord(
        connector_id=SQLConnector.connector_id,
        source_uri=source_uri,
        text=content,
        mime_type="text/plain",
        hints={
            "title": title,
            "external_id": external_id,
            "parser": "sql_passthrough",
        },
        agent_id=agent_id,
        metadata={
            "source_label": source_label,
            "cursor_column": cursor_column,
            "cursor_value": cursor_value,
            "external_id": external_id,
            "row_columns": list(mapping.keys()),
        },
    )


def _sentinel_min_for(cursor_column: str) -> Any:
    """
    A value guaranteed to be less than any plausible cursor value. Numeric
    cursors compare correctly against this; for text/datetime cursors the
    caller must pass an explicit ``initial_cursor``.
    """
    return -(2**63)
