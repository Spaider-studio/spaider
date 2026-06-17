"""
Connectors API: observability endpoints for the Connector Framework.

Currently exposes a single endpoint:
  GET /connectors/{connector_id}/status
    Returns the last-run statistics for the named connector.
    Polled by the Studio UI to show live record counts and error state.
"""
from __future__ import annotations

import logging

from fastapi import APIRouter, HTTPException

from app.connectors import ConnectorStats, get_connector_stats, get_global_registry

logger = logging.getLogger(__name__)

router = APIRouter()


@router.get(
    "/{connector_id}/status",
    response_model=ConnectorStats,
    summary="Get last-run statistics for a connector",
    description=(
        "Returns the statistics recorded after the most recent run of the named "
        "connector.  If the connector has never been run since the server started "
        "(or if it was just registered), the response is an idle zero-count record.\n\n"
        "**Intended use:** the Studio UI polls this endpoint every few seconds "
        "after triggering a connector run to display record counts and error state "
        "without blocking the user."
    ),
)
async def get_connector_status(connector_id: str) -> ConnectorStats:
    """
    Return the last-run stats for *connector_id*.

    Returns 404 if the connector_id is not registered in the global registry
    (i.e. it is an unknown connector, not just one that has never been run).
    """
    registry = get_global_registry()
    if registry is None or registry.get(connector_id) is None:
        raise HTTPException(
            status_code=404,
            detail=(
                f"Connector '{connector_id}' is not registered. "
                f"Known connectors: {registry.connector_ids if registry else []}"
            ),
        )
    return get_connector_stats(connector_id)
