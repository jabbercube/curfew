"""Tests for /v1/auth/* (login, logout, me, change-password)."""

from __future__ import annotations

from pathlib import Path

from curfew.models import AuditLog, AuditTargetKind, UserSession
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select


def _engine(db: Path):
    return create_engine(f"sqlite:///{db}")


def _create_user(
    client: TestClient,
    auth: dict[str, str],
    username: str = "alice",
    password: str = "test1234",
    role: str = "admin",
) -> None:
    r = client.post(
        "/v1/users",
        json={"username": username, "password": password, "role": role},
        headers=auth,
    )
    assert r.status_code == 201, r.text


# --- login --------------------------------------------------------------------


def test_login_success_sets_cookie(client: TestClient, auth: dict[str, str]) -> None:
    _create_user(client, auth)
    r = client.post("/v1/auth/login", json={"username": "alice", "password": "test1234"})
    assert r.status_code == 204
    assert "curfew_session" in r.cookies


def test_login_persists_session_row(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    _create_user(client, auth)
    client.post("/v1/auth/login", json={"username": "alice", "password": "test1234"})
    with Session(_engine(configured_db)) as s:
        rows = s.exec(select(UserSession)).all()
    assert len(rows) == 1


def test_login_wrong_password_401(client: TestClient, auth: dict[str, str]) -> None:
    _create_user(client, auth)
    r = client.post("/v1/auth/login", json={"username": "alice", "password": "wrong"})
    assert r.status_code == 401


def test_login_unknown_username_401(client: TestClient) -> None:
    r = client.post("/v1/auth/login", json={"username": "ghost", "password": "anything"})
    assert r.status_code == 401


def test_login_audited(client: TestClient, configured_db: Path, auth: dict[str, str]) -> None:
    _create_user(client, auth)
    client.post("/v1/auth/login", json={"username": "alice", "password": "test1234"})
    with Session(_engine(configured_db)) as s:
        rows = s.exec(select(AuditLog).where(AuditLog.action == "auth.login")).all()
    assert len(rows) == 1
    assert rows[0].actor == "alice"


# --- logout -------------------------------------------------------------------


def test_logout_clears_cookie_and_deletes_session(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    _create_user(client, auth)
    client.post("/v1/auth/login", json={"username": "alice", "password": "test1234"})
    r = client.post("/v1/auth/logout")
    assert r.status_code == 204
    with Session(_engine(configured_db)) as s:
        assert s.exec(select(UserSession)).all() == []
    # Subsequent /me without cookie is unauthenticated.
    r2 = client.get("/v1/auth/me")
    assert r2.status_code == 401


def test_logout_with_root_token_idempotent(client: TestClient, auth: dict[str, str]) -> None:
    """Logout via root bearer is a no-op for the session table but still 204s."""
    r = client.post("/v1/auth/logout", headers=auth)
    assert r.status_code == 204


# --- me -----------------------------------------------------------------------


def test_me_root_token(client: TestClient, auth: dict[str, str]) -> None:
    r = client.get("/v1/auth/me", headers=auth)
    assert r.status_code == 200
    assert r.json() == {"kind": "root", "username": None, "role": "admin"}


def test_me_user_session(client: TestClient, auth: dict[str, str]) -> None:
    _create_user(client, auth, username="alice", role="manager")
    client.post("/v1/auth/login", json={"username": "alice", "password": "test1234"})
    r = client.get("/v1/auth/me")
    assert r.status_code == 200
    assert r.json() == {"kind": "user", "username": "alice", "role": "manager"}


def test_me_unauthenticated(client: TestClient) -> None:
    assert client.get("/v1/auth/me").status_code == 401


# --- change-password ----------------------------------------------------------


def test_change_password_success(client: TestClient, auth: dict[str, str]) -> None:
    _create_user(client, auth, username="alice", password="oldpassword")
    client.post("/v1/auth/login", json={"username": "alice", "password": "oldpassword"})
    r = client.post(
        "/v1/auth/change-password",
        json={"current_password": "oldpassword", "new_password": "newpassword"},
    )
    assert r.status_code == 204
    # Old password no longer works.
    client.post("/v1/auth/logout")
    bad = client.post("/v1/auth/login", json={"username": "alice", "password": "oldpassword"})
    assert bad.status_code == 401
    # New password works.
    good = client.post("/v1/auth/login", json={"username": "alice", "password": "newpassword"})
    assert good.status_code == 204


def test_change_password_wrong_current_401(client: TestClient, auth: dict[str, str]) -> None:
    _create_user(client, auth)
    client.post("/v1/auth/login", json={"username": "alice", "password": "test1234"})
    r = client.post(
        "/v1/auth/change-password",
        json={"current_password": "wrong", "new_password": "newpassword"},
    )
    assert r.status_code == 401


def test_change_password_too_short_422(client: TestClient, auth: dict[str, str]) -> None:
    _create_user(client, auth)
    client.post("/v1/auth/login", json={"username": "alice", "password": "test1234"})
    r = client.post(
        "/v1/auth/change-password",
        json={"current_password": "test1234", "new_password": "short"},
    )
    assert r.status_code == 422


def test_change_password_root_token_400(client: TestClient, auth: dict[str, str]) -> None:
    """Root token has no password to change."""
    r = client.post(
        "/v1/auth/change-password",
        json={"current_password": "x", "new_password": "newpassword"},
        headers=auth,
    )
    assert r.status_code == 400


def test_change_password_unauthenticated(client: TestClient) -> None:
    r = client.post(
        "/v1/auth/change-password",
        json={"current_password": "x", "new_password": "newpassword"},
    )
    assert r.status_code == 401


# --- audit shape --------------------------------------------------------------


def test_user_actor_writes_username_to_audit(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    """Per-user-authenticated writes record the username, not 'operator'."""
    _create_user(client, auth, username="admin2", role="admin")
    client.post("/v1/auth/login", json={"username": "admin2", "password": "test1234"})
    client.post(
        "/v1/users",
        json={"username": "kid1", "password": "test1234"},
    )
    with Session(_engine(configured_db)) as s:
        rows = s.exec(
            select(AuditLog)
            .where(AuditLog.target_kind == AuditTargetKind.USER)
            .where(AuditLog.action == "user.create")
        ).all()
    # Two user.create rows: one for admin2 (recorded as 'operator', root token)
    # and one for kid1 (recorded as 'admin2', the user actor).
    actors = {r.actor for r in rows if r.target_id == "kid1"}
    assert actors == {"admin2"}
