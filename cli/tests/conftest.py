"""Keeps tests away from the real config file and any server on this machine."""
from __future__ import annotations

import pytest


@pytest.fixture(autouse=True)
def isolate_config(tmp_path, monkeypatch):
    monkeypatch.setenv("SPAIDER_CONFIG", str(tmp_path / "isolated-config.json"))
    monkeypatch.setattr(
        "spaider_cli.lib.api.bootstrap", lambda token, org, owner: {"api_key": "sk-bootstrapped"}, raising=True
    )
