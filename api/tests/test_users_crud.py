"""Tests for user CRUD beyond create + get (covered by test_locks.py)."""

from __future__ import annotations

from pathlib import Path

from curfew.models import AuditLog, AuditTargetKind, UserLock
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select


def _engine(db: Path):
    return create_engine(f"sqlite:///{db}")


# --- list ---------------------------------------------------------------------


def test_list_empty(client: TestClient, auth: dict[str, str]) -> None:
    r = client.get("/v1/users", headers=auth)
    assert r.status_code == 200
    assert r.json() == []


def test_list_alphabetical(client: TestClient, auth: dict[str, str]) -> None:
    for username in ["zoe", "alice", "kid1"]:
        client.post("/v1/users", json={"username": username}, headers=auth)
    r = client.get("/v1/users", headers=auth)
    assert [u["username"] for u in r.json()] == ["alice", "kid1", "zoe"]


def test_list_unauthenticated(client: TestClient) -> None:
    assert client.get("/v1/users").status_code == 401


# --- patch --------------------------------------------------------------------


def test_patch_role_only(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={"role": "manager"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "manager"
    assert body["managed"] is True  # unchanged
    assert body["username"] == "kid1"  # unchanged


def test_patch_managed_flag(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={"managed": False}, headers=auth)
    assert r.json()["managed"] is False


def test_patch_username_rename(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={"username": "alice"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["username"] == "alice"
    # Original username is now 404
    assert client.get("/v1/users/kid1", headers=auth).status_code == 404
    assert client.get("/v1/users/alice", headers=auth).status_code == 200


def test_patch_empty_body_is_noop(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={}, headers=auth)
    assert r.status_code == 200
    assert r.json()["username"] == "kid1"


def test_patch_username_collision_409(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    client.post("/v1/users", json={"username": "kid2"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={"username": "kid2"}, headers=auth)
    assert r.status_code == 409


def test_patch_missing_user_404(client: TestClient, auth: dict[str, str]) -> None:
    r = client.patch("/v1/users/ghost", json={"role": "admin"}, headers=auth)
    assert r.status_code == 404


def test_patch_unauthenticated(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    assert client.patch("/v1/users/kid1", json={"role": "admin"}).status_code == 401


# --- delete -------------------------------------------------------------------


def test_delete_returns_204(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    r = client.delete("/v1/users/kid1", headers=auth)
    assert r.status_code == 204
    assert client.get("/v1/users/kid1", headers=auth).status_code == 404


def test_delete_missing_user_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.delete("/v1/users/ghost", headers=auth).status_code == 404


def test_delete_user_with_devices_409(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows", "owner": "kid1"},
        headers=auth,
    )
    r = client.delete("/v1/users/kid1", headers=auth)
    assert r.status_code == 409
    assert "owns one or more devices" in r.json()["detail"]


def test_delete_user_with_lock_cascades(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    """A user_lock row is metadata of the user; cascading the delete is fine."""
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    client.post("/v1/users/kid1/lock", headers=auth)
    # Sanity: lock row exists.
    with Session(_engine(configured_db)) as s:
        assert s.exec(select(UserLock)).first() is not None

    r = client.delete("/v1/users/kid1", headers=auth)
    assert r.status_code == 204
    with Session(_engine(configured_db)) as s:
        assert s.exec(select(UserLock)).first() is None


def test_delete_unauthenticated(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    assert client.delete("/v1/users/kid1").status_code == 401


# --- audit --------------------------------------------------------------------


def test_audit_records_update_and_delete(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    client.patch("/v1/users/kid1", json={"role": "manager"}, headers=auth)
    client.delete("/v1/users/kid1", headers=auth)
    with Session(_engine(configured_db)) as s:
        rows = s.exec(
            select(AuditLog)
            .where(AuditLog.target_kind == AuditTargetKind.USER)
            .order_by(AuditLog.id)  # type: ignore[arg-type]
        ).all()
    assert [r.action for r in rows] == ["user.create", "user.update", "user.delete"]
    update_row = rows[1]
    assert update_row.payload == {"role": "manager"}
