"""Tests for the FastAPI app skeleton.

Covers: health endpoint (unauthed, 200, db round-trip), OpenAPI exposure,
auth dependency (401 missing/wrong, 200 right), and the ``actor`` set on
``request.state`` for protected routes.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from curfew.config import reset_config_cache
from curfew.db import reset_engine_cache
from curfew_api.app import create_app
from curfew_api.auth import Operator
from fastapi import APIRouter
from fastapi.testclient import TestClient


@pytest.fixture
def configured_db(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[Path]:
    """Spin up a fresh migrated SQLite DB and configure env to point at it."""
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

    reset_config_cache()
    reset_engine_cache()


@pytest.fixture
def client(configured_db: Path) -> TestClient:
    return TestClient(create_app())


@pytest.fixture
def client_with_protected_stub(configured_db: Path) -> TestClient:
    """An app with an extra ``/v1/_stub`` route gated by ``Operator``."""
    app = create_app()
    stub = APIRouter()

    @stub.get("/v1/_stub")
    def _stub(actor: Operator) -> dict[str, str]:
        return {"actor": actor}

    app.include_router(stub)
    return TestClient(app)


def test_health_unauthenticated(client: TestClient) -> None:
    r = client.get("/v1/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok"}


def test_openapi_unauthenticated(client: TestClient) -> None:
    r = client.get("/v1/openapi.json")
    assert r.status_code == 200
    assert r.json()["info"]["title"] == "curfew-core"


def test_protected_requires_token(client_with_protected_stub: TestClient) -> None:
    r = client_with_protected_stub.get("/v1/_stub")
    assert r.status_code == 401
    assert r.headers.get("WWW-Authenticate", "").lower() == "bearer"


def test_protected_rejects_wrong_token(client_with_protected_stub: TestClient) -> None:
    r = client_with_protected_stub.get("/v1/_stub", headers={"Authorization": "Bearer wrong-token"})
    assert r.status_code == 401


def test_protected_accepts_right_token(client_with_protected_stub: TestClient) -> None:
    r = client_with_protected_stub.get("/v1/_stub", headers={"Authorization": "Bearer test-token"})
    assert r.status_code == 200
    assert r.json() == {"actor": "operator"}


def test_protected_rejects_non_bearer_scheme(client_with_protected_stub: TestClient) -> None:
    r = client_with_protected_stub.get("/v1/_stub", headers={"Authorization": "Basic dXNlcjpwYXNz"})
    assert r.status_code == 401


def test_app_factory_surfaces_required_token(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No CURFEW_ROOT_TOKEN → app factory raises a clear validation error."""
    from pydantic import ValidationError

    monkeypatch.chdir(tmp_path)
    monkeypatch.delenv("CURFEW_ROOT_TOKEN", raising=False)
    reset_config_cache()
    with pytest.raises(ValidationError):
        create_app()
    reset_config_cache()
