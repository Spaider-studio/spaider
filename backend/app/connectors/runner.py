"""
ConnectorRunner — secure orchestrator for the Connector Framework.

Responsibilities
----------------
1. Load ``ConnectorConfig``, ``ConnectorSecret``, and ``ConnectorRunState``
   from Postgres for a given ``connector_config_id``.
2. Retrieve the registered connector instance from ``ConnectorRegistry``.
3. Decrypt credentials **strictly in-memory, just-in-time** via
   ``SecretsService``.  The plaintext dict is scoped to the generator frame
   and is never logged, yielded, stored, or allowed to escape.
4. Delegate validation to ``connector.validate(config_json, credentials)``.
5. Stream records from ``connector.read(config_json, credentials, cursor)``
   and yield them to the caller one at a time.
6. Track the incremental cursor dynamically as records arrive.
7. Persist the final ``ConnectorRunState`` (cursor, timestamp, status) to
   Postgres in a ``finally`` block — guaranteed even on generator close.

Security invariants
-------------------
- **Credentials never leave this module.** They are decrypted inside the
  generator, passed by reference to connector methods, and held nowhere else.
- **Error messages are sanitised.** The ``_sanitise_error`` helper strips any
  string that looks like a secret (Bearer tokens, sk- keys, passwords) before
  writing to ``ConnectorRunState.last_error``.
- **No credential logging.** Every log call in this module uses only
  structural fields (connector_id, agent_id, record counts) — never
  credential keys or values.

Connector interface contract
----------------------------
Connectors executed through this runner must implement two async methods
**in addition to** the existing ``BaseConnector.run()`` interface:

``async validate(config_json: dict, credentials: dict) -> None``
    Raise ``ValueError`` (or a subclass) if the config or credentials are
    invalid.  Must NOT make network calls; it is a fast pre-flight check.

``async read(config_json: dict, credentials: dict, cursor: str | None)
    -> AsyncIterator[ConnectorRecord]``
    Yield one ``ConnectorRecord`` per document.  The last record's
    ``record_id`` (or an explicit ``cursor`` field in ``ConnectorRecord.metadata``)
    is used to advance ``ConnectorRunState.cursor`` after each yield.
"""
from __future__ import annotations

import logging
import re
from datetime import datetime, timezone
from typing import AsyncIterator

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.connectors import ConnectorRecord, ConnectorRegistry
from app.models.connector import ConnectorConfig, ConnectorRunState, ConnectorSecret
from app.services.secrets_service import SecretsService, get_secrets_service

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Patterns used to scrub credentials from error messages
# ---------------------------------------------------------------------------

# Matches: Bearer <token>, sk-<anything>, passwords in URLs, generic key=value
# pairs whose key names look credential-ish.
_SECRET_PATTERNS: list[re.Pattern[str]] = [
    re.compile(r"Bearer\s+\S+", re.IGNORECASE),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{8,}\b"),
    re.compile(r"(?i)(password|passwd|secret|token|api[_-]?key|access[_-]?key"
               r"|refresh[_-]?token|client[_-]?secret)\s*[=:]\s*\S+"),
    re.compile(r"://[^:]+:[^@]+@"),   # credentials embedded in URLs
]

_REDACTION = "[REDACTED]"


def _sanitise_error(raw: str) -> str:
    """
    Strip credential-looking substrings from *raw* before writing to the DB.

    This is a defence-in-depth measure — connectors should never include
    credentials in exception messages, but this guard ensures a programming
    mistake in a connector never causes a credential leak via the error log.
    """
    sanitised = raw
    for pattern in _SECRET_PATTERNS:
        sanitised = pattern.sub(_REDACTION, sanitised)
    # Hard cap: error messages stored in Postgres are capped at 2 KB
    return sanitised[:2048]


# ---------------------------------------------------------------------------
# ConnectorRunner
# ---------------------------------------------------------------------------


class ConnectorRunnerError(Exception):
    """Raised when the runner cannot proceed (misconfiguration, missing rows)."""


class ConnectorRunner:
    """
    Stateless orchestrator.  One instance per process is sufficient, but it
    is safe to instantiate per-request.

    Parameters
    ----------
    registry:
        ``ConnectorRegistry`` instance that holds all registered connectors.
        Defaults to the global registry set by ``set_global_registry()``.
    secrets_svc:
        ``SecretsService`` instance.  Defaults to the process singleton.
    """

    def __init__(
        self,
        registry: ConnectorRegistry | None = None,
        secrets_svc: SecretsService | None = None,
    ) -> None:
        from app.connectors import get_global_registry

        self._registry: ConnectorRegistry = registry or get_global_registry() or ConnectorRegistry()
        self._secrets: SecretsService = secrets_svc or get_secrets_service()

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def run_connector(
        self,
        connector_config_id: str,
        db_session: AsyncSession,
    ) -> AsyncIterator[ConnectorRecord]:
        """
        Async generator — yields ``ConnectorRecord`` objects produced by the
        connector.  Safe to consume with ``async for``.

        Parameters
        ----------
        connector_config_id:
            Primary key of the ``ConnectorConfig`` row to execute.
        db_session:
            Live ``AsyncSession``.  The caller retains ownership of the
            session lifecycle; the runner does NOT commit or close it except
            for the final ``RunState`` upsert inside ``finally``.

        Yields
        ------
        ConnectorRecord
            One per document produced by the connector.

        Raises
        ------
        ConnectorRunnerError
            If the config row is missing, the connector type is not registered,
            or validation fails.  These are raised *before* any yielding starts.

        Notes
        -----
        The ``finally`` block runs whether the caller exhausts the generator,
        closes it early (``aclose()``), or an exception propagates.  The
        ``ConnectorRunState`` is always written before the generator terminates.
        """
        return self._run(connector_config_id, db_session)

    # ------------------------------------------------------------------
    # Internal generator (split from run_connector so callers get the
    # async generator object without needing to await run_connector itself)
    # ------------------------------------------------------------------

    async def _run(
        self,
        connector_config_id: str,
        db_session: AsyncSession,
    ) -> AsyncIterator[ConnectorRecord]:
        # ── 1. Load ConnectorConfig ────────────────────────────────────────
        config: ConnectorConfig | None = await db_session.get(
            ConnectorConfig, connector_config_id
        )
        if config is None:
            raise ConnectorRunnerError(
                f"ConnectorConfig '{connector_config_id}' not found in Postgres."
            )

        logger.info(
            "runner: starting connector_config_id=%s type_id=%s agent_id=%s",
            config.id, config.type_id, config.agent_id,
        )

        # ── 2. Load ConnectorSecret rows ───────────────────────────────────
        secret_rows_result = await db_session.execute(
            select(ConnectorSecret).where(
                ConnectorSecret.connector_id == connector_config_id
            )
        )
        secret_rows = secret_rows_result.scalars().all()

        # ── 3. Load (or create) ConnectorRunState ──────────────────────────
        run_state: ConnectorRunState | None = await db_session.get(
            ConnectorRunState, connector_config_id
        )
        if run_state is None:
            run_state = ConnectorRunState(
                connector_id=connector_config_id,
                cursor=None,
                last_run_at=None,
                last_run_status=None,
                last_error=None,
            )
            db_session.add(run_state)
            logger.debug(
                "runner: no existing RunState for %s — created fresh row.",
                connector_config_id,
            )

        # ── 4. Look up connector in registry ──────────────────────────────
        connector = self._registry.get(config.type_id)
        if connector is None:
            raise ConnectorRunnerError(
                f"Connector type '{config.type_id}' is not registered. "
                f"Available types: {self._registry.connector_ids}"
            )

        # ── 5. Decrypt credentials just-in-time ───────────────────────────
        # The plaintext dict is local to this frame.  It is passed by
        # reference to connector methods and goes out of scope when this
        # generator is garbage-collected.  It is never logged or yielded.
        credentials: dict = {}
        for row in secret_rows:
            try:
                partial = self._secrets.decrypt(row.ciphertext, row.nonce)
                credentials[row.key_name] = partial.get(row.key_name, partial)
            except Exception as exc:
                raise ConnectorRunnerError(
                    f"Failed to decrypt secret '{row.key_name}' for connector "
                    f"'{connector_config_id}': {type(exc).__name__}"
                    # Intentionally NOT including exc message — may contain key material
                ) from exc

        # ── 6. Validate (pre-flight, no network calls) ─────────────────────
        try:
            await connector.validate(config.config_json, credentials)  # type: ignore[attr-defined]
        except Exception as exc:
            raise ConnectorRunnerError(
                f"Connector validation failed for '{config.type_id}': "
                f"{_sanitise_error(str(exc))}"
            ) from exc

        # ── 7. Stream records ──────────────────────────────────────────────
        records_yielded = 0
        final_status = "success"

        try:
            async for record in connector.read(  # type: ignore[attr-defined]
                config.config_json,
                credentials,
                run_state.cursor,
            ):
                # Advance cursor dynamically — prefer explicit cursor in
                # record.metadata, fall back to record_id as a bookmark.
                new_cursor: str | None = (
                    record.metadata.get("cursor")
                    or record.record_id
                )
                run_state.cursor = new_cursor
                records_yielded += 1

                yield record

        except Exception as exc:
            final_status = "failed"
            sanitised_msg = _sanitise_error(str(exc))
            run_state.last_run_status = "failed"
            run_state.last_error = sanitised_msg
            logger.error(
                "runner: connector read failed connector_config_id=%s type_id=%s "
                "records_yielded=%d error=%s",
                connector_config_id,
                config.type_id,
                records_yielded,
                sanitised_msg,
                # exc_info omitted deliberately: stack traces may contain repr()
                # of objects that hold credential data.
            )
            raise

        finally:
            # ── 8. Persist RunState — guaranteed even on early close ───────
            # Only update status/timestamp if there was no explicit failure
            # (the failure branch already set them above).
            if final_status != "failed":
                run_state.last_run_status = "success"
                run_state.last_error = None

            run_state.last_run_at = datetime.now(timezone.utc)

            try:
                # merge() handles both insert (new row) and update (existing row)
                # without requiring a separate existence check.
                await db_session.merge(run_state)
                await db_session.commit()
                logger.info(
                    "runner: RunState committed connector_config_id=%s "
                    "status=%s cursor=%r records_yielded=%d",
                    connector_config_id,
                    run_state.last_run_status,
                    run_state.cursor,
                    records_yielded,
                )
            except Exception as persist_exc:
                # Persistence failure must not mask the original error
                logger.error(
                    "runner: FAILED to persist RunState for %s: %s",
                    connector_config_id,
                    persist_exc,
                )

            # Belt-and-suspenders: clear the in-memory credentials dict so
            # the GC can reclaim the plaintext values as early as possible.
            credentials.clear()
