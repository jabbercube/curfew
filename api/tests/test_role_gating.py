"""Tests for role-tiered access control on existing routes.

Matrix (per the per-user-auth slice):

| Route                          | member | manager | admin | root |
|--------------------------------|--------|---------|-------|------|
| GET    /v1/users               | 200    | 200     | 200   | 200  |
| POST   /v1/users               | 403    | 403     | 201   | 201  |
| PATCH  /v1/users/{u}           | 403    | 403     | 200   | 200  |
| DELETE /v1/users/{u}           | 403    | 403     | 204   | 204  |
| POST   /v1/devices             | 403    | 403     | 201   | 201  |
| POST   /v1/users/{u}/lock      | 403    | 200     | 200   | 200  |
| POST   /v1/users/{u}/unlock    | 403    | 200     | 200   | 200  |
| GET    /v1/settings            | 403    | 403     | 200   | 200  |
| PATCH  /v1/settings            | 403    | 403     | 200   | 200  |
| GET    /v1/system/snapshot     | 403    | 403     | 200   | 200  |

Reads on collections (users/devices/apps + status) stay open to any
authenticated user — those tests live in their respective per-resource files.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

# --- writes: user CRUD --------------------------------------------------------


def test_member_cannot_create_user(member_client: TestClient) -> None:
    r = member_client.post("/v1/users", json={"username": "x", "password": "test1234"})
    assert r.status_code == 403


def test_manager_cannot_create_user(manager_client: TestClient) -> None:
    r = manager_client.post("/v1/users", json={"username": "x", "password": "test1234"})
    assert r.status_code == 403


def test_admin_can_create_user(admin_client: TestClient) -> None:
    r = admin_client.post("/v1/users", json={"username": "kid1", "password": "test1234"})
    assert r.status_code == 201


def test_member_cannot_patch_user(member_client: TestClient, auth: dict[str, str]) -> None:
    member_client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = member_client.patch("/v1/users/kid1", json={"role": "manager"})
    assert r.status_code == 403


def test_member_cannot_delete_user(member_client: TestClient, auth: dict[str, str]) -> None:
    member_client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = member_client.delete("/v1/users/kid1")
    assert r.status_code == 403


# --- writes: devices + apps ---------------------------------------------------


def test_manager_cannot_create_device(manager_client: TestClient) -> None:
    r = manager_client.post("/v1/devices", json={"slug": "rig", "type": "pc", "os": "windows"})
    assert r.status_code == 403


def test_member_cannot_create_app(member_client: TestClient) -> None:
    r = member_client.post("/v1/apps", json={"slug": "steam"})
    assert r.status_code == 403


# --- locks: manager+ ----------------------------------------------------------


def test_member_cannot_lock(member_client: TestClient, auth: dict[str, str]) -> None:
    member_client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = member_client.post("/v1/users/kid1/lock")
    assert r.status_code == 403


def test_manager_can_lock(manager_client: TestClient, auth: dict[str, str]) -> None:
    manager_client.post(
        "/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth
    )
    r = manager_client.post("/v1/users/kid1/lock")
    assert r.status_code == 200
    assert r.json()["locked"] is True


def test_admin_can_unlock(admin_client: TestClient) -> None:
    admin_client.post("/v1/users", json={"username": "kid1", "password": "test1234"})
    admin_client.post("/v1/users/kid1/lock")
    r = admin_client.post("/v1/users/kid1/unlock")
    assert r.status_code == 200


# --- settings + system: admin only --------------------------------------------


def test_member_cannot_read_settings(member_client: TestClient) -> None:
    assert member_client.get("/v1/settings").status_code == 403


def test_manager_cannot_read_settings(manager_client: TestClient) -> None:
    assert manager_client.get("/v1/settings").status_code == 403


def test_admin_can_read_settings(admin_client: TestClient) -> None:
    assert admin_client.get("/v1/settings").status_code == 200


def test_manager_cannot_read_snapshot(manager_client: TestClient) -> None:
    assert manager_client.get("/v1/system/snapshot").status_code == 403


def test_admin_can_read_snapshot(admin_client: TestClient) -> None:
    assert admin_client.get("/v1/system/snapshot").status_code == 200


# --- root token still works as admin -----------------------------------------


def test_root_token_still_creates_users(client: TestClient, auth: dict[str, str]) -> None:
    r = client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    assert r.status_code == 201


def test_root_token_can_lock(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.post("/v1/users/kid1/lock", headers=auth)
    assert r.status_code == 200


def test_root_token_can_read_snapshot(client: TestClient, auth: dict[str, str]) -> None:
    assert client.get("/v1/system/snapshot", headers=auth).status_code == 200


# --- reads still open to any authenticated -----------------------------------


def test_member_can_list_users(member_client: TestClient) -> None:
    assert member_client.get("/v1/users").status_code == 200


def test_member_can_get_status(member_client: TestClient, auth: dict[str, str]) -> None:
    member_client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    assert member_client.get("/v1/users/kid1/status").status_code == 200
