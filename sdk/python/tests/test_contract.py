"""Contract guard — the SDK response models must cover the backend's schemas.

The contract is a committed snapshot of the backend's OpenAPI document
(``contract/openapi.json``), refreshed with ``make refresh-openapi`` against a
running backend. If the backend adds or renames a *required* response field,
this test fails until the SDK model (and the snapshot) are updated.

This is the durable fix for the drift that previously shipped a broken client
(``Edge.source_id`` vs the API's ``source``, ``QueryResult.text`` vs ``answer``,
the swarm models, …): hand-written SDK models can no longer silently diverge
from the API they target.
"""
from __future__ import annotations

import json
from pathlib import Path

import pytest

from spaider import models

SNAPSHOT = Path(__file__).resolve().parents[1] / "contract" / "openapi.json"

# SDK response model -> the backend OpenAPI component schema it mirrors.
MAPPING: dict[type, str] = {
    models.QueryResult: "QueryResult",
    models.Edge: "Edge",
    models.Node: "Node",
    models.GraphPayload: "GraphResponse",
    models.IngestResult: "IngestQueuedResponse",
    models.SwarmQueryResult: "SwarmQueryResponse",
    models.SwarmConnection: "SwarmConnection",
}


def _component_schemas() -> dict:
    return json.loads(SNAPSHOT.read_text(encoding="utf-8"))["components"]["schemas"]


def test_snapshot_present():
    assert SNAPSHOT.exists(), "missing OpenAPI snapshot — run `make refresh-openapi`"


@pytest.mark.parametrize(
    "model,schema_name",
    list(MAPPING.items()),
    ids=[m.__name__ for m in MAPPING],
)
def test_sdk_model_covers_required_response_fields(model: type, schema_name: str):
    schemas = _component_schemas()
    assert schema_name in schemas, (
        f"{schema_name} is not in the OpenAPI snapshot — the backend may have "
        f"renamed it; refresh and update the mapping."
    )
    required = set(schemas[schema_name].get("required", []))
    sdk_fields = set(model.model_fields.keys())
    missing = required - sdk_fields
    assert not missing, (
        f"{model.__name__} is missing field(s) the backend `{schema_name}` "
        f"guarantees: {sorted(missing)}. Update the SDK model (and the snapshot)."
    )
