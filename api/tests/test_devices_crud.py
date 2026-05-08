"""Tests for /v1/devices CRUD."""

from __future__ import annotations

from pathlib import Path

from curfew.models import Agent, AuditLog, AuditTargetKind, Device
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select


def _engine(db: Path):
    return create_engine(f"sqlite:///{db}")


def _create_user(client: TestClient, auth: dict[str, str], username: str = "kid1") -> int:
    """Create a user and return its id."""
    r = client.post("/v1/users", json={"username": username, "password": "test1234"}, headers=auth)
    assert r.status_code == 201
    user_id = r.json()["id"]
    assert isinstance(user_id, int)
    return user_id


# --- create -------------------------------------------------------------------


def test_create_with_owner(client: TestClient, auth: dict[str, str]) -> None:
    user_id = _create_user(client, auth)
    r = client.post(
        "/v1/devices",
        json={
            "slug": "rig",
            "type": "pc",
            "os": "windows",
            "owner_id": user_id,
            "mac": ["aa:bb:cc:dd:ee:ff"],
        },
        headers=auth,
    )
    assert r.status_code == 201
    body = r.json()
    assert body == {
        "id": body["id"],
        "slug": "rig",
        "type": "pc",
        "os": "windows",
        "owner_id": user_id,
        "mac": ["aa:bb:cc:dd:ee:ff"],
        "managed": True,
    }
    assert isinstance(body["id"], int)


def test_create_without_owner_is_shared(client: TestClient, auth: dict[str, str]) -> None:
    r = client.post(
        "/v1/devices",
        json={"slug": "livingroomtv", "type": "tv", "os": "android"},
        headers=auth,
    )
    assert r.status_code == 201
    assert r.json()["owner_id"] is None


def test_create_unknown_owner_id_404(client: TestClient, auth: dict[str, str]) -> None:
    r = client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows", "owner_id": 999},
        headers=auth,
    )
    assert r.status_code == 404
    assert "owner_id 999 not found" in r.json()["detail"]


def test_create_duplicate_slug_409(client: TestClient, auth: dict[str, str]) -> None:
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows"},
        headers=auth,
    )
    r = client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "phone", "os": "android"},
        headers=auth,
    )
    assert r.status_code == 409


def test_create_unauthenticated(client: TestClient) -> None:
    r = client.post("/v1/devices", json={"slug": "rig", "type": "pc", "os": "windows"})
    assert r.status_code == 401


# --- list / get ---------------------------------------------------------------


def test_list_alphabetical(client: TestClient, auth: dict[str, str]) -> None:
    for slug in ["zoo", "alpha", "rig"]:
        client.post(
            "/v1/devices",
            json={"slug": slug, "type": "pc", "os": "windows"},
            headers=auth,
        )
    r = client.get("/v1/devices", headers=auth)
    assert [d["slug"] for d in r.json()] == ["alpha", "rig", "zoo"]


def test_get_returns_owner_id(client: TestClient, auth: dict[str, str]) -> None:
    user_id = _create_user(client, auth)
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows", "owner_id": user_id},
        headers=auth,
    )
    body = client.get("/v1/devices/rig", headers=auth).json()
    assert body["owner_id"] == user_id


def test_get_missing_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.get("/v1/devices/ghost", headers=auth).status_code == 404


# --- patch --------------------------------------------------------------------


def test_patch_slug_rename(client: TestClient, auth: dict[str, str]) -> None:
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows"},
        headers=auth,
    )
    r = client.patch("/v1/devices/rig", json={"slug": "gaming-rig"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["slug"] == "gaming-rig"
    assert client.get("/v1/devices/rig", headers=auth).status_code == 404
    assert client.get("/v1/devices/gaming-rig", headers=auth).status_code == 200


def test_patch_replace_mac(client: TestClient, auth: dict[str, str]) -> None:
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows", "mac": ["aa:bb"]},
        headers=auth,
    )
    r = client.patch(
        "/v1/devices/rig",
        json={"mac": ["cc:dd", "ee:ff"]},
        headers=auth,
    )
    assert r.json()["mac"] == ["cc:dd", "ee:ff"]


def test_patch_set_owner(client: TestClient, auth: dict[str, str]) -> None:
    user_id = _create_user(client, auth)
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows"},
        headers=auth,
    )
    r = client.patch("/v1/devices/rig", json={"owner_id": user_id}, headers=auth)
    assert r.json()["owner_id"] == user_id


def test_patch_clear_owner(client: TestClient, auth: dict[str, str]) -> None:
    """Sending owner_id: null clears the owner; sending nothing leaves it."""
    user_id = _create_user(client, auth)
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows", "owner_id": user_id},
        headers=auth,
    )
    r = client.patch("/v1/devices/rig", json={"owner_id": None}, headers=auth)
    assert r.status_code == 200
    assert r.json()["owner_id"] is None


def test_patch_unknown_owner_id_404(client: TestClient, auth: dict[str, str]) -> None:
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows"},
        headers=auth,
    )
    r = client.patch("/v1/devices/rig", json={"owner_id": 999}, headers=auth)
    assert r.status_code == 404


def test_patch_missing_404(client: TestClient, auth: dict[str, str]) -> None:
    assert (
        client.patch("/v1/devices/ghost", json={"managed": False}, headers=auth).status_code == 404
    )


def test_patch_slug_conflict_409(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/devices", json={"slug": "a", "type": "pc", "os": "windows"}, headers=auth)
    client.post("/v1/devices", json={"slug": "b", "type": "pc", "os": "windows"}, headers=auth)
    r = client.patch("/v1/devices/a", json={"slug": "b"}, headers=auth)
    assert r.status_code == 409


# --- delete -------------------------------------------------------------------


def test_delete_returns_204(client: TestClient, auth: dict[str, str]) -> None:
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows"},
        headers=auth,
    )
    assert client.delete("/v1/devices/rig", headers=auth).status_code == 204
    assert client.get("/v1/devices/rig", headers=auth).status_code == 404


def test_delete_with_agent_409(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows"},
        headers=auth,
    )
    # Insert an Agent row directly (no agent-install endpoint yet).
    with Session(_engine(configured_db)) as s:
        device = s.exec(select(Device).where(Device.slug == "rig")).one()
        s.add(Agent(device_id=device.id, type="windows-agent", config={}))
        s.commit()

    r = client.delete("/v1/devices/rig", headers=auth)
    assert r.status_code == 409
    assert "agent installed" in r.json()["detail"]


def test_delete_missing_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.delete("/v1/devices/ghost", headers=auth).status_code == 404


# --- audit --------------------------------------------------------------------


def test_audit_records_create_update_delete(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    user_id = _create_user(client, auth)
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows"},
        headers=auth,
    )
    client.patch("/v1/devices/rig", json={"owner_id": user_id}, headers=auth)
    client.delete("/v1/devices/rig", headers=auth)
    with Session(_engine(configured_db)) as s:
        rows = s.exec(
            select(AuditLog)
            .where(AuditLog.target_kind == AuditTargetKind.DEVICE)
            .order_by(AuditLog.id)  # type: ignore[arg-type]
        ).all()
    assert [r.action for r in rows] == ["device.create", "device.update", "device.delete"]
    update_payload = rows[1].payload
    assert update_payload == {"owner_id": user_id}
