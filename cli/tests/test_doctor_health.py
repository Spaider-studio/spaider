"""`spaider doctor` must not say "All clear" while a service is broken."""
from __future__ import annotations

from unittest.mock import patch

from spaider_cli.commands import doctor


def _body(**services):
    return {"status": "degraded", "healthy": True, "services": services}


def test_a_service_that_is_down_is_reported_even_though_the_api_answers():
    body = _body(neo4j="ok", redis="ok", kafka="unavailable", vector_index="ok")

    with patch.object(doctor.api_client, "health_report", return_value=body):
        result = doctor.check_backend_health()

    assert result.status != "ok"
    assert "kafka" in result.detail


def test_a_missing_vector_index_is_reported():
    body = _body(neo4j="ok", redis="ok", kafka="ok", vector_index="unavailable")

    with patch.object(doctor.api_client, "health_report", return_value=body):
        result = doctor.check_backend_health()

    assert result.status != "ok"
    assert "vector_index" in result.detail


def test_services_that_are_switched_off_are_not_a_problem():
    body = {"status": "ok", "healthy": True, "services": {"neo4j": "ok", "kafka": "disabled", "vector_index": "ok"}}

    with patch.object(doctor.api_client, "health_report", return_value=body):
        result = doctor.check_backend_health()

    assert result.status == "ok"


def test_an_unreachable_api_is_still_a_failure():
    with patch.object(doctor.api_client, "health_report", return_value=None):
        result = doctor.check_backend_health()

    assert result.status == "fail"
