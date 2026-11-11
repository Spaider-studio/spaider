"""Shared fixtures. ``bootstrap()`` runs first so the reused SpAIder modules
see the portable environment; every test gets a fresh app root in a temp
directory whose name contains spaces and non-ASCII characters on purpose."""

from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from spaider_foldable.bootstrap import bootstrap  # noqa: E402

bootstrap()

from spaider_foldable.db import Database  # noqa: E402
from spaider_foldable.paths import PortablePaths  # noqa: E402

REAL = os.environ.get("SPAIDER_FOLDABLE_REAL_TESTS") == "1"
RUNTIME_DIR = os.environ.get("SPAIDER_FOLDABLE_RUNTIME_DIR")
CHAT_MODEL = os.environ.get("SPAIDER_FOLDABLE_TEST_CHAT_MODEL")
EMBED_MODEL = os.environ.get("SPAIDER_FOLDABLE_TEST_EMBED_MODEL")


@pytest.fixture
def app_root(tmp_path: Path) -> Path:
    root = tmp_path / "SpAIder Töst Ü"  # spaces + umlauts on purpose
    root.mkdir()
    return root


@pytest.fixture
def paths(app_root: Path) -> PortablePaths:
    p = PortablePaths.from_app_root(app_root)
    p.ensure()
    return p


@pytest.fixture
def db(paths: PortablePaths):
    d = Database(paths.db_path)
    d.open()
    yield d
    d.close()


def requires_real():
    return pytest.mark.skipif(
        not (REAL and RUNTIME_DIR and CHAT_MODEL),
        reason="set SPAIDER_FOLDABLE_REAL_TESTS=1, SPAIDER_FOLDABLE_RUNTIME_DIR and SPAIDER_FOLDABLE_TEST_CHAT_MODEL",
    )
