"""Tests for ``GET/POST/PATCH/DELETE /v1/plugins`` (assignment CRUD).

Uses the ``client_with_repo_plugins`` fixture so the shipped
``reftest_plugin`` is discovered and we can assign it without
synthesising a plugin folder per test.
"""

from __future__ import annotations

from pathlib import Path

from curfew.models import AuditLog, AuditTargetKind
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select


def _engine(db: Path):  # type: ignore[no-untyped-def]
    return create_engine(f"sqlite:///{db}")


def _ok_config(tmp_path: Path) -> dict[str, str]:
    """A reftest config that works — points at a writable sentinel path."""
    return {"sentinel_path": str(tmp_path / "sentinel")}


# --- list / GET --------------------------------------------------------------


def test_list_unauthenticated(client_with_repo_plugins: TestClient) -> None:
    assert client_with_repo_plugins.get("/v1/plugins").status_code == 401


def test_list_empty(client_with_repo_plugins: TestClient, auth: dict[str, str]) -> None:
    r = client_with_repo_plugins.get("/v1/plugins", headers=auth)
    assert r.status_code == 200
    assert r.json() == []


def test_list_after_create(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": ["*"]},
        headers=auth,
    )
    r = client_with_repo_plugins.get("/v1/plugins", headers=auth)
    assert r.status_code == 200
    [item] = r.json()
    assert item["type"] == "reftest_plugin"
    assert item["instance_id"] == "default"
    assert item["users"] == ["*"]
    assert item["paused"] is False


# --- POST --------------------------------------------------------------------


def test_create_returns_201(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    r = client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": ["kid1"]},
        headers=auth,
    )
    assert r.status_code == 201
    body = r.json()
    assert body["type"] == "reftest_plugin"
    assert body["instance_id"] == "default"
    assert body["users"] == ["kid1"]


def test_create_unknown_type_404(
    client_with_repo_plugins: TestClient, auth: dict[str, str]
) -> None:
    r = client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "no_such_plugin", "config": {}, "users": []},
        headers=auth,
    )
    assert r.status_code == 404
    assert "no_such_plugin" in r.json()["detail"]


def test_create_invalid_config_422(
    client_with_repo_plugins: TestClient, auth: dict[str, str]
) -> None:
    """reftest_plugin requires sentinel_path; missing it is a 422."""
    r = client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": {}, "users": []},
        headers=auth,
    )
    assert r.status_code == 422


def test_create_duplicate_409(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    body = {"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": []}
    r = client_with_repo_plugins.post("/v1/plugins", json=body, headers=auth)
    assert r.status_code == 201
    r = client_with_repo_plugins.post("/v1/plugins", json=body, headers=auth)
    assert r.status_code == 409
    assert "already assigned" in r.json()["detail"]


def test_create_with_explicit_instance_id(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    """Two instances of the same type with different instance_ids coexist."""
    body1 = {
        "type": "reftest_plugin",
        "instance_id": "alpha",
        "config": _ok_config(tmp_path),
        "users": ["kid1"],
    }
    body2 = {
        "type": "reftest_plugin",
        "instance_id": "beta",
        "config": {"sentinel_path": str(tmp_path / "sentinel-b")},
        "users": ["kid2"],
    }
    assert client_with_repo_plugins.post("/v1/plugins", json=body1, headers=auth).status_code == 201
    assert client_with_repo_plugins.post("/v1/plugins", json=body2, headers=auth).status_code == 201

    r = client_with_repo_plugins.get("/v1/plugins", headers=auth)
    assert {p["instance_id"] for p in r.json()} == {"alpha", "beta"}


def test_create_audited(
    client_with_repo_plugins: TestClient,
    auth: dict[str, str],
    tmp_path: Path,
    configured_db_with_repo_plugins: Path,
) -> None:
    client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": ["kid1"]},
        headers=auth,
    )
    with Session(_engine(configured_db_with_repo_plugins)) as s:
        rows = list(s.exec(select(AuditLog).where(AuditLog.action == "plugin.assign")))
    assert len(rows) == 1
    assert rows[0].target_id == "reftest_plugin/default"
    assert rows[0].target_kind == AuditTargetKind.PLUGIN
    assert rows[0].payload == {"users": ["kid1"]}


# --- PATCH -------------------------------------------------------------------


def test_patch_users(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": ["kid1"]},
        headers=auth,
    )
    r = client_with_repo_plugins.patch(
        "/v1/plugins/reftest_plugin/default",
        json={"users": ["kid1", "kid2"]},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["users"] == ["kid1", "kid2"]


def test_patch_paused(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": ["*"]},
        headers=auth,
    )
    r = client_with_repo_plugins.patch(
        "/v1/plugins/reftest_plugin/default",
        json={"paused": True},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["paused"] is True


def test_patch_config_invalid_422(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": ["*"]},
        headers=auth,
    )
    # sentinel_path must be a string; an int fails validation.
    r = client_with_repo_plugins.patch(
        "/v1/plugins/reftest_plugin/default",
        json={"config": {"sentinel_path": 42}},
        headers=auth,
    )
    assert r.status_code == 422


def test_patch_404(client_with_repo_plugins: TestClient, auth: dict[str, str]) -> None:
    r = client_with_repo_plugins.patch(
        "/v1/plugins/reftest_plugin/default",
        json={"users": []},
        headers=auth,
    )
    assert r.status_code == 404


def test_patch_empty_payload_no_op(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": ["*"]},
        headers=auth,
    )
    r = client_with_repo_plugins.patch(
        "/v1/plugins/reftest_plugin/default",
        json={},
        headers=auth,
    )
    assert r.status_code == 200
    assert r.json()["users"] == ["*"]


# --- DELETE ------------------------------------------------------------------


def test_delete_204(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": ["*"]},
        headers=auth,
    )
    r = client_with_repo_plugins.delete("/v1/plugins/reftest_plugin/default", headers=auth)
    assert r.status_code == 204
    r = client_with_repo_plugins.get("/v1/plugins", headers=auth)
    assert r.json() == []


def test_delete_404(client_with_repo_plugins: TestClient, auth: dict[str, str]) -> None:
    r = client_with_repo_plugins.delete("/v1/plugins/reftest_plugin/default", headers=auth)
    assert r.status_code == 404


# --- Auth gating -------------------------------------------------------------


def test_create_requires_admin(
    client_with_repo_plugins: TestClient,
    auth: dict[str, str],
    tmp_path: Path,
) -> None:
    """A manager cookie can read the list but can't assign."""
    # Bootstrap a manager via root token.
    r = client_with_repo_plugins.post(
        "/v1/users",
        json={"username": "mgr", "password": "test1234", "role": "manager"},
        headers=auth,
    )
    assert r.status_code == 201
    r = client_with_repo_plugins.post(
        "/v1/auth/login", json={"username": "mgr", "password": "test1234"}
    )
    assert r.status_code == 204

    # No bearer in headers; client carries the manager session cookie.
    r = client_with_repo_plugins.post(
        "/v1/plugins",
        json={"type": "reftest_plugin", "config": _ok_config(tmp_path), "users": []},
    )
    assert r.status_code == 403
