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
def client(configured_db: Path) -> Iterator[TestClient]:
    """TestClient as a context manager so FastAPI lifespan fires.

    Without the ``with`` block, ``startup``/``shutdown`` don't run and
    ``app.state.plugin_runtime`` is never built — every route that
    schedules reconciles would AttributeError. Yielding from the
    fixture makes tests use the same lifecycle production does.
    """
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def configured_db_with_repo_plugins(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> Iterator[Path]:
    """``configured_db`` variant that also points the loader at the repo plugins.

    Tests that exercise plugin assignment / reconcile dispatch need a real
    discovered plugin (the shipped ``reftest_plugin``). Setting
    ``CURFEW_PLUGINS_DIRS`` to the repo's ``plugins/`` directory makes
    discovery find it; the rest of the setup mirrors ``configured_db``.
    """
    repo_root = Path(__file__).resolve().parents[2]
    monkeypatch.setenv("CURFEW_PLUGINS_DIRS", str(repo_root / "plugins"))

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
def client_with_repo_plugins(configured_db_with_repo_plugins: Path) -> Iterator[TestClient]:
    """``client`` variant with ``reftest_plugin`` discovered.

    Used by plugin assignment / reconcile tests so they don't have to
    synthesise a plugin folder per test.
    """
    with TestClient(create_app()) as c:
        yield c


@pytest.fixture
def auth() -> dict[str, str]:
    return {"Authorization": "Bearer test-token"}


# --- Role-typed user clients -------------------------------------------------
#
# Each fixture creates a user with the given role (using the root token), logs
# them in via /v1/auth/login, and yields a TestClient that already carries the
# session cookie for that user. Tests for role gating use these to assert that
# e.g. a manager cannot hit an admin-only endpoint.


def _login_as(client: TestClient, auth: dict[str, str], username: str, role: str) -> TestClient:
    """Create a user with the given role + log them in; return a cookie-bearing client."""
    r = client.post(
        "/v1/users",
        json={"username": username, "password": "test1234", "role": role},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    r = client.post("/v1/auth/login", json={"username": username, "password": "test1234"})
    assert r.status_code == 204, r.text
    # TestClient persists cookies on the same instance, so the same client is now
    # authenticated as `username`.
    return client


@pytest.fixture
def admin_client(client: TestClient, auth: dict[str, str]) -> TestClient:
    return _login_as(client, auth, "admin1", "admin")


@pytest.fixture
def manager_client(client: TestClient, auth: dict[str, str]) -> TestClient:
    return _login_as(client, auth, "manager1", "manager")


@pytest.fixture
def member_client(client: TestClient, auth: dict[str, str]) -> TestClient:
    return _login_as(client, auth, "member1", "member")
