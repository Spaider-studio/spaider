"""What the audit list says to someone who may not read any audit log."""
from __future__ import annotations

import pytest

URL = "/api/v1/replay/workflows"


@pytest.mark.asyncio
async def test_a_reader_asking_for_all_audit_logs_is_told_they_have_no_access(auth_world):
    """It answered 200 with an empty list, which the page showed as "no runs yet"."""
    kb = await auth_world.create_kb("kb")
    reader = await auth_world.member(kb["id"], "reader")

    response = await auth_world.client.get(URL, headers=auth_world.headers(reader["api_key"]))

    assert response.status_code == 403
    assert "audit" in response.json()["detail"].lower()


@pytest.mark.asyncio
async def test_an_auditor_gets_the_list(auth_world):
    kb = await auth_world.create_kb("kb")
    auditor = await auth_world.member(kb["id"], "auditor")

    response = await auth_world.client.get(URL, headers=auth_world.headers(auditor["api_key"]))

    assert response.status_code == 200


@pytest.mark.asyncio
async def test_the_owner_of_an_org_with_no_knowledge_bases_gets_an_empty_list_not_an_error(auth_world):
    response = await auth_world.client.get(URL, headers=auth_world.headers(auth_world.owner_key))

    assert response.status_code == 200
    assert response.json()["total"] == 0
