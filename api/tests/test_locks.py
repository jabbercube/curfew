"""Integration tests for the lock feature.

Covers: user create + read, user lock/unlock, status (locked/unlocked/missing/
unmanaged), device status (404 + non-404 path), audit log writes.

Shared ``configured_db`` / ``client`` / ``auth`` fixtures live in ``conftest.py``.
"""

from __future__ import annotations

from pathlib import Path

from curfew.models import AuditLog, AuditTargetKind, Device, DeviceOS, DeviceType
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select


def _engine(db: Path):
    return create_engine(f"sqlite:///{db}")


# --- User create + read --------------------------------------------------------


def test_create_user_returns_201_with_id(client: TestClient, auth: dict[str, str]) -> None:
    r = client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    assert r.status_code == 201
    body = r.json()
    assert body["username"] == "kid1"
    assert body["role"] == "member"
    assert body["managed"] is True
    assert isinstance(body["id"], int)


def test_create_user_409_on_duplicate(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    assert r.status_code == 409


def test_create_user_unauthenticated(client: TestClient) -> None:
    r = client.post("/v1/users", json={"username": "kid1", "password": "test1234"})
    assert r.status_code == 401


def test_get_user(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.get("/v1/users/kid1", headers=auth)
    assert r.status_code == 200
    assert r.json()["username"] == "kid1"


def test_get_user_404(client: TestClient, auth: dict[str, str]) -> None:
    r = client.get("/v1/users/ghost", headers=auth)
    assert r.status_code == 404


# --- Lock / unlock + status ---------------------------------------------------


def test_status_unlocked_initially(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.get("/v1/users/kid1/status", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"locked": False, "reasons": []}


def test_lock_then_status_returns_locked(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.post("/v1/users/kid1/lock", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"locked": True, "reasons": [{"kind": "manual_lock"}]}

    r = client.get("/v1/users/kid1/status", headers=auth)
    assert r.json() == {"locked": True, "reasons": [{"kind": "manual_lock"}]}


def test_unlock_clears_lock(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    client.post("/v1/users/kid1/lock", headers=auth)
    r = client.post("/v1/users/kid1/unlock", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"locked": False, "reasons": []}


def test_lock_idempotent(client: TestClient, auth: dict[str, str]) -> None:
    """Calling lock twice keeps the user locked, no error."""
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    client.post("/v1/users/kid1/lock", headers=auth)
    r = client.post("/v1/users/kid1/lock", headers=auth)
    assert r.status_code == 200
    assert r.json()["locked"] is True


def test_lock_missing_user_404(client: TestClient, auth: dict[str, str]) -> None:
    r = client.post("/v1/users/ghost/lock", headers=auth)
    assert r.status_code == 404


def test_status_skips_unmanaged_user(client: TestClient, auth: dict[str, str]) -> None:
    """managed=False users always return unlocked even if the lock row is true."""
    client.post(
        "/v1/users",
        json={"username": "adult", "role": "manager", "managed": False, "password": "test1234"},
        headers=auth,
    )
    # Lock the user directly (the endpoint also works on unmanaged users —
    # the rule pipeline is what skips them).
    r = client.post("/v1/users/adult/lock", headers=auth)
    # The lock toggle returns the pipeline result, which skips unmanaged users.
    assert r.json() == {"locked": False, "reasons": []}
    r = client.get("/v1/users/adult/status", headers=auth)
    assert r.json() == {"locked": False, "reasons": []}


# --- Device status ------------------------------------------------------------


def test_device_status_404_when_missing(client: TestClient, auth: dict[str, str]) -> None:
    r = client.get("/v1/devices/ghostrig/status", headers=auth)
    assert r.status_code == 404


def test_device_status_empty_pipeline_in_kernel(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    """Device-scope pipeline is empty in the kernel; existing device returns
    {locked: false, reasons: []}."""
    with Session(_engine(configured_db)) as s:
        s.add(Device(slug="rig", type=DeviceType.PC, os=DeviceOS.WINDOWS))
        s.commit()
    r = client.get("/v1/devices/rig/status", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"locked": False, "reasons": []}


# --- Audit log ----------------------------------------------------------------


def test_audit_records_user_create(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    with Session(_engine(configured_db)) as s:
        rows = s.exec(select(AuditLog).where(AuditLog.action == "user.create")).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.actor == "operator"
    assert row.target_kind is AuditTargetKind.USER
    assert row.target_id == "kid1"
    assert row.payload == {"role": "member", "managed": True}


def test_audit_records_lock_and_unlock(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    client.post("/v1/users/kid1/lock", headers=auth)
    client.post("/v1/users/kid1/unlock", headers=auth)
    with Session(_engine(configured_db)) as s:
        actions = [
            row.action
            for row in s.exec(
                select(AuditLog).order_by(AuditLog.id)  # type: ignore[arg-type]
            ).all()
        ]
    assert actions == ["user.create", "user.lock", "user.unlock"]
