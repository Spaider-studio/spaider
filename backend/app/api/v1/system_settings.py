"""
System Settings API — global admin controls for the SpAIder platform.

Endpoints
---------
GET  /api/v1/system/settings            → read global settings (auto_reflection, engine_version)
POST /api/v1/system/settings/reflection → toggle autonomous reflection engine (kill-switch)
POST /api/v1/system/settings/engine     → switch engine version ("v1" | "v2")
POST /api/v1/system/consolidate → trigger graph_maintenance DAG via Airflow REST
"""
from __future__ import annotations

import logging
from datetime import datetime, timezone
from typing import Literal, Optional

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, field_validator

from app.config import settings

logger = logging.getLogger(__name__)

router = APIRouter()

# ---------------------------------------------------------------------------
# Pydantic models
# ---------------------------------------------------------------------------


class SystemSettingsResponse(BaseModel):
    auto_reflection: bool
    engine_version: Literal["v1", "v2"]


class ReflectionToggle(BaseModel):
    enabled: bool


class EngineVersionToggle(BaseModel):
    version: Literal["v1", "v2"]

    @field_validator("version")
    @classmethod
    def version_must_be_valid(cls, v: str) -> str:
        if v not in ("v1", "v2"):
            raise ValueError('engine version must be "v1" or "v2"')
        return v


# ---------------------------------------------------------------------------
# Lazy driver accessor — reuses the graph service singleton started in main.py
# ---------------------------------------------------------------------------


def _get_driver():
    """Return the Neo4j async driver from the shared GraphService singleton."""
    from app.services.graph_service import GraphService
    svc = GraphService()
    return svc._driver


# ---------------------------------------------------------------------------
# Cypher statements
# ---------------------------------------------------------------------------

# Idempotent singleton creation — sets both fields on first call only.
_INIT_CYPHER = """
MERGE (s:SystemSettings {id: "global"})
ON CREATE SET
    s.auto_reflection = false,
    s.engine_version  = "v1"
RETURN
    s.auto_reflection AS auto_reflection,
    coalesce(s.engine_version, "v1") AS engine_version
"""

_GET_CYPHER = """
MATCH (s:SystemSettings {id: "global"})
RETURN
    s.auto_reflection AS auto_reflection,
    coalesce(s.engine_version, "v1") AS engine_version
"""

_SET_REFLECTION_CYPHER = """
MATCH (s:SystemSettings {id: "global"})
SET s.auto_reflection = $enabled
RETURN
    s.auto_reflection AS auto_reflection,
    coalesce(s.engine_version, "v1") AS engine_version
"""

_SET_ENGINE_CYPHER = """
MATCH (s:SystemSettings {id: "global"})
SET s.engine_version = $version
RETURN
    s.auto_reflection AS auto_reflection,
    s.engine_version  AS engine_version
"""


# ---------------------------------------------------------------------------
# Internal helper
# ---------------------------------------------------------------------------


async def _ensure_settings_node() -> SystemSettingsResponse:
    """
    MERGE the global settings singleton if absent.
    Returns the current values for all settings fields.
    """
    driver = _get_driver()
    async with driver.session() as session:
        result = await session.run(_INIT_CYPHER)
        record = await result.single()

    if record is None:
        # Should never happen with MERGE, but guard anyway
        return SystemSettingsResponse(auto_reflection=False, engine_version="v1")

    return SystemSettingsResponse(
        auto_reflection=bool(record["auto_reflection"]),
        engine_version=record["engine_version"] or "v1",
    )


async def _read_settings() -> SystemSettingsResponse:
    """
    Read current settings without creating the node.
    Falls back to _ensure_settings_node if the node is missing.
    """
    driver = _get_driver()
    async with driver.session() as session:
        result = await session.run(_GET_CYPHER)
        record = await result.single()

    if record is None:
        return await _ensure_settings_node()

    return SystemSettingsResponse(
        auto_reflection=bool(record["auto_reflection"]),
        engine_version=record["engine_version"] or "v1",
    )


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------


@router.get("/settings", response_model=SystemSettingsResponse)
async def get_system_settings():
    """
    Read the global system settings from Neo4j.
    Creates the singleton node on first call if it does not yet exist.

    Returns:
        auto_reflection: Whether the autonomous Reflection Engine is active.
        engine_version:  Active graph engine — "v1" (classic RAG) or "v2" (Cognitive Graph).
    """
    try:
        return await _ensure_settings_node()
    except Exception as exc:
        logger.exception("Failed to read system settings: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Could not read system settings from Neo4j: {exc}",
        )


@router.post("/settings/reflection", response_model=SystemSettingsResponse)
async def set_reflection_toggle(body: ReflectionToggle):
    """
    Enable or disable the autonomous Reflection Engine (Hippocampus).

    The toggle is persisted on the global SystemSettings singleton in Neo4j
    and is checked by the background scheduler every 5 minutes.
    Returns 404 if the settings node is missing (call GET first to initialise).
    """
    driver = _get_driver()
    try:
        async with driver.session() as session:
            result = await session.run(_SET_REFLECTION_CYPHER, enabled=body.enabled)
            record = await result.single()
    except Exception as exc:
        logger.exception("Failed to update reflection toggle: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Could not update reflection setting in Neo4j: {exc}",
        )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "SystemSettings node not found. "
                "Call GET /api/v1/system/settings first to initialise it."
            ),
        )

    new_value = bool(record["auto_reflection"])
    logger.info("Reflection Engine toggled → %s", "ENABLED" if new_value else "DISABLED")

    return SystemSettingsResponse(
        auto_reflection=new_value,
        engine_version=record["engine_version"] or "v1",
    )


@router.post("/settings/engine", response_model=SystemSettingsResponse)
async def set_engine_version(body: EngineVersionToggle):
    """
    Switch the active graph engine between V1 and V2.

    - **v1**: Classic RAG graph — all edges rendered with uniform width.
    - **v2**: Cognitive Graph — edge width reflects `utility_weight` synapse strength.

    The setting is persisted in Neo4j and immediately visible to all connected clients
    via the EngineContext (frontend polls or reads on mount).
    Returns 404 if the settings node is missing (call GET first to initialise).
    """
    driver = _get_driver()
    try:
        async with driver.session() as session:
            result = await session.run(_SET_ENGINE_CYPHER, version=body.version)
            record = await result.single()
    except Exception as exc:
        logger.exception("Failed to update engine version: %s", exc)
        raise HTTPException(
            status_code=500,
            detail=f"Could not update engine version in Neo4j: {exc}",
        )

    if record is None:
        raise HTTPException(
            status_code=404,
            detail=(
                "SystemSettings node not found. "
                "Call GET /api/v1/system/settings first to initialise it."
            ),
        )

    logger.info("Engine version switched → %s", body.version.upper())

    return SystemSettingsResponse(
        auto_reflection=bool(record["auto_reflection"]),
        engine_version=record["engine_version"] or "v1",
    )


# ---------------------------------------------------------------------------
# On-demand consolidation trigger
# ---------------------------------------------------------------------------


class ConsolidateRequest(BaseModel):
    """Optional knobs for the on-demand consolidate trigger."""
    note: Optional[str] = None  # operator-provided reason; surfaced in DAG run conf


class ConsolidateResponse(BaseModel):
    dag_id: str
    dag_run_id: str
    state: str
    status_url: str


@router.post("/consolidate", response_model=ConsolidateResponse)
async def trigger_consolidate(body: Optional[ConsolidateRequest] = None):
    """Trigger the graph_maintenance Airflow DAG immediately, off its
    normal schedule. Useful right after a heavy ingest, or when the
    operator wants to run consolidation without waiting for Sunday 03:00 UTC.

    Behaviour
    ---------
    - Returns the new ``dag_run_id`` and a ``status_url`` the operator can
      poll (or open in the Airflow UI).
    - Returns **503 Service Unavailable** when ``AIRFLOW_BASE_URL`` is
      unset or unreachable. The error body points at the CLI fallback
      (``python -m app.scripts.run_consolidation``) which works without
      Airflow.

    Authentication: re-uses ``AIRFLOW_USERNAME``/``AIRFLOW_PASSWORD`` from
    settings (basic auth — Airflow's stable v2 default). Airflow's other
    auth schemes (Kerberos, JWT) would need a separate adapter.
    """
    if not settings.airflow_base_url:
        raise HTTPException(
            status_code=503,
            detail=(
                "Airflow REST API is not configured (AIRFLOW_BASE_URL unset). "
                "On-demand DAG triggering is unavailable. Use the CLI fallback "
                "instead: `python -m app.scripts.run_consolidation`."
            ),
        )

    import httpx

    base = settings.airflow_base_url.rstrip("/")
    dag_id = settings.airflow_consolidate_dag_id
    url = f"{base}/api/v1/dags/{dag_id}/dagRuns"

    # Airflow accepts either v1 or v2 API; v1 (the stable one in Airflow
    # 2.x) takes a logical_date and a conf dict. Airflow 3 will use v2;
    # leaving v1 here so we work against the docker-compose.airflow.yml
    # bundled in the repo (Airflow 2.10.2).
    payload: dict = {
        "logical_date": datetime.now(timezone.utc).isoformat(),
    }
    if body and body.note:
        payload["conf"] = {"note": body.note, "triggered_by": "system/consolidate"}

    auth = (settings.airflow_username, settings.airflow_password)
    try:
        async with httpx.AsyncClient(timeout=10.0) as client:
            resp = await client.post(url, json=payload, auth=auth)
    except (httpx.ConnectError, httpx.TimeoutException) as exc:
        raise HTTPException(
            status_code=503,
            detail=(
                f"Airflow at {base} is unreachable: {exc}. "
                "Check that the Airflow webserver is running "
                "(``make airflow-up``), or fall back to the CLI: "
                "``python -m app.scripts.run_consolidation``."
            ),
        )

    if resp.status_code != 200:
        logger.error(
            "Airflow trigger failed: status=%d body=%s", resp.status_code, resp.text[:200],
        )
        raise HTTPException(
            status_code=502,
            detail=f"Airflow rejected the trigger (HTTP {resp.status_code}): {resp.text[:300]}",
        )

    data = resp.json()
    dag_run_id = data.get("dag_run_id", "(unknown)")
    state = data.get("state", "queued")
    status_url = f"{base}/dags/{dag_id}/grid?dag_run_id={dag_run_id}"
    logger.info(
        "Triggered Airflow DAG %s — dag_run_id=%s state=%s",
        dag_id, dag_run_id, state,
    )
    return ConsolidateResponse(
        dag_id=dag_id, dag_run_id=dag_run_id, state=state, status_url=status_url,
    )
