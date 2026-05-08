"""Shared fixtures for api/tests/*.

The lock-feature tests (test_locks.py) and the CRUD tests need the same setup:
a fresh migrated SQLite per test, env vars pointing at it, and a TestClient
built on the resulting app. Defined here so each test file doesn't re-roll the
boilerplate.

test_app.py keeps its own fixtures because it tests the auth dep with a
custom-mounted stub router; that's a one-off shape.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from curfew.config import reset_config_cache
from curfew.db import reset_engine_cache
from curfew.rules import user_scope
from curfew_api.app import create_app
from fastapi.testclient import TestClient


@pytest.fixture
def configured_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Fresh migrated SQLite + env pointing at it; reset caches on teardown."""
    db = tmp_path / "test.sqlite"
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("CURFEW_ROOT_TOKEN", "test-token")
    monkeypatch.setenv("CURFEW_DB_PATH", str(db))
    reset_config_cache()
    reset_engine_cache()

    api_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")

    yield db

    user_scope.reset()
    reset_config_cache()
    reset_engine_cache()


@pytest.fixture
def client(configured_db: Path) -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}
