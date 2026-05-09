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
        client.post("/v1/users", json={"username": username, "password": "test1234"}, headers=auth)
    r = client.get("/v1/users", headers=auth)
    assert [u["username"] for u in r.json()] == ["alice", "kid1", "zoe"]


def test_list_unauthenticated(client: TestClient) -> None:
    assert client.get("/v1/users").status_code == 401


# --- patch --------------------------------------------------------------------


def test_patch_role_only(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={"role": "manager"}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["role"] == "manager"
    assert body["managed"] is True  # unchanged
    assert body["username"] == "kid1"  # unchanged


def test_patch_managed_flag(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={"managed": False}, headers=auth)
    assert r.json()["managed"] is False


def test_patch_username_rename(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={"username": "alice"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["username"] == "alice"
    # Original username is now 404
    assert client.get("/v1/users/kid1", headers=auth).status_code == 404
    assert client.get("/v1/users/alice", headers=auth).status_code == 200


def test_patch_empty_body_is_noop(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={}, headers=auth)
    assert r.status_code == 200
    assert r.json()["username"] == "kid1"


def test_patch_username_collision_409(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    client.post("/v1/users", json={"username": "kid2", "password": "test1234"}, headers=auth)
    r = client.patch("/v1/users/kid1", json={"username": "kid2"}, headers=auth)
    assert r.status_code == 409


def test_patch_missing_user_404(client: TestClient, auth: dict[str, str]) -> None:
    r = client.patch("/v1/users/ghost", json={"role": "admin"}, headers=auth)
    assert r.status_code == 404


def test_patch_unauthenticated(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    assert client.patch("/v1/users/kid1", json={"role": "admin"}).status_code == 401


# --- patch managed → unmanaged auto-unlocks ----------------------------------
#
# Invariant: an unmanaged user is never locked at the DB level. Setting
# ``managed=false`` on a currently-locked user clears the lock + audits a
# ``user.unlock`` row + dispatches plugin reconcile so external state
# (smart plug, DNS sinkhole, etc.) follows. The opposite direction
# (``managed=true``) leaves the lock state alone — operators chose to
# manage; we don't auto-anything.


def test_patch_managed_false_clears_lock(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.post("/v1/users/kid1/lock", headers=auth)
    assert r.json()["locked"] is True

    r = client.patch("/v1/users/kid1", json={"managed": False}, headers=auth)
    assert r.status_code == 200
    assert r.json()["managed"] is False

    # Status now returns unlocked (managed=False makes the rule pipeline
    # skip, but additionally the manual_lock flag itself is cleared).
    r = client.get("/v1/users/kid1/status", headers=auth)
    assert r.json() == {"locked": False, "reasons": []}
    with Session(_engine(configured_db)) as s:
        lock = s.exec(select(UserLock)).first()
        assert lock is not None
        assert lock.manual_lock is False


def test_patch_managed_false_audits_implicit_unlock(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    """Audit trail shows ``user.unlock`` *before* ``user.update``.

    The "first unlock the user, then unmanage them" semantic — operator
    can read the audit log and see the order of events as the kernel
    actually performed them.
    """
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    client.post("/v1/users/kid1/lock", headers=auth)
    client.patch("/v1/users/kid1", json={"managed": False}, headers=auth)

    with Session(_engine(configured_db)) as s:
        actions = [
            r.action
            for r in s.exec(
                select(AuditLog)
                .where(AuditLog.target_kind == AuditTargetKind.USER)
                .order_by(AuditLog.id)  # type: ignore[arg-type]
            )
        ]
    # create, lock, then unlock-then-update from the PATCH.
    assert actions == ["user.create", "user.lock", "user.unlock", "user.update"]
    with Session(_engine(configured_db)) as s:
        unlock_row = s.exec(
            select(AuditLog).where(AuditLog.action == "user.unlock").order_by(AuditLog.id.desc())  # type: ignore[union-attr]
        ).first()
        assert unlock_row is not None
        assert unlock_row.payload == {"reason": "managed_to_unmanaged"}


def test_patch_managed_false_no_unlock_audit_when_not_locked(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    """User wasn't locked → no implicit unlock is recorded."""
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    client.patch("/v1/users/kid1", json={"managed": False}, headers=auth)

    with Session(_engine(configured_db)) as s:
        actions = [
            r.action
            for r in s.exec(
                select(AuditLog)
                .where(AuditLog.target_kind == AuditTargetKind.USER)
                .order_by(AuditLog.id)  # type: ignore[arg-type]
            )
        ]
    assert actions == ["user.create", "user.update"]


def test_patch_managed_true_doesnt_modify_lock(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    """Unmanaged → managed is a pure flag flip; any (stale) lock stays.

    The rule pipeline reads ``managed=False`` for unmanaged users and
    skips them entirely, so a stale lock has no observable effect — but
    the operator's PATCH shouldn't silently mutate it either.
    """
    client.post(
        "/v1/users",
        json={"username": "kid1", "password": "test1234", "managed": False},
        headers=auth,
    )
    # Plant a stale lock row directly so we have something to *not* clear.
    with Session(_engine(configured_db)) as s:
        from datetime import UTC, datetime

        from curfew.models import User

        u = s.exec(select(User).where(User.username == "kid1")).one()
        s.add(UserLock(user_id=u.id, manual_lock=True, set_at=datetime.now(UTC)))
        s.commit()

    r = client.patch("/v1/users/kid1", json={"managed": True}, headers=auth)
    assert r.status_code == 200
    assert r.json()["managed"] is True

    with Session(_engine(configured_db)) as s:
        lock = s.exec(select(UserLock)).first()
        assert lock is not None
        assert lock.manual_lock is True  # untouched


# --- delete -------------------------------------------------------------------


def test_delete_returns_204(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    r = client.delete("/v1/users/kid1", headers=auth)
    assert r.status_code == 204
    assert client.get("/v1/users/kid1", headers=auth).status_code == 404


def test_delete_missing_user_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.delete("/v1/users/ghost", headers=auth).status_code == 404


def test_delete_user_with_devices_409(client: TestClient, auth: dict[str, str]) -> None:
    user_id = client.post(
        "/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth
    ).json()["id"]
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows", "owner_id": user_id},
        headers=auth,
    )
    r = client.delete("/v1/users/kid1", headers=auth)
    assert r.status_code == 409
    assert "owns one or more devices" in r.json()["detail"]


def test_delete_user_with_lock_cascades(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    """A user_lock row is metadata of the user; cascading the delete is fine."""
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    client.post("/v1/users/kid1/lock", headers=auth)
    # Sanity: lock row exists.
    with Session(_engine(configured_db)) as s:
        assert s.exec(select(UserLock)).first() is not None

    r = client.delete("/v1/users/kid1", headers=auth)
    assert r.status_code == 204
    with Session(_engine(configured_db)) as s:
        assert s.exec(select(UserLock)).first() is None


def test_delete_unauthenticated(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
    assert client.delete("/v1/users/kid1").status_code == 401


# --- audit --------------------------------------------------------------------


def test_audit_records_update_and_delete(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.post("/v1/users", json={"username": "kid1", "password": "test1234"}, headers=auth)
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
