"""Tests for /v1/system/snapshot (operator diagnostic dump)."""

from __future__ import annotations

from pathlib import Path

from curfew.models import Agent, AgentToken, Device
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select


def _engine(db: Path):
    return create_engine(f"sqlite:///{db}")


# --- shape --------------------------------------------------------------------


def test_empty_snapshot_has_all_top_level_keys(client: TestClient, auth: dict[str, str]) -> None:
    r = client.get("/v1/system/snapshot", headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert set(body.keys()) == {
        "users",
        "devices",
        "apps",
        "agents",
        "plugins",
        "user_locks",
        "manifests",
        "settings",
        "counts",
    }
    # All collections empty on a fresh install...
    for key in ("users", "devices", "apps", "agents", "plugins", "user_locks", "manifests"):
        assert body[key] == []
    # ...except settings, which is the seeded singleton.
    assert body["settings"]["agent_tick_seconds"] == 60
    # Counts present and zero.
    assert body["counts"] == {"audit_log": 0, "agent_tokens": 0}


# --- populated state ----------------------------------------------------------


def test_snapshot_includes_users_devices_apps(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    user_id = client.post("/v1/users", json={"username": "kid2"}, headers=auth).json()["id"]
    client.post(
        "/v1/devices",
        json={"slug": "rig", "type": "pc", "os": "windows", "owner_id": user_id},
        headers=auth,
    )
    client.post("/v1/apps", json={"slug": "steam"}, headers=auth)

    body = client.get("/v1/system/snapshot", headers=auth).json()
    assert [u["username"] for u in body["users"]] == ["kid1", "kid2"]
    assert [d["slug"] for d in body["devices"]] == ["rig"]
    assert body["devices"][0]["owner_id"] == user_id
    assert [a["slug"] for a in body["apps"]] == ["steam"]


def test_snapshot_includes_user_lock_after_lock(client: TestClient, auth: dict[str, str]) -> None:
    user_id = client.post("/v1/users", json={"username": "kid1"}, headers=auth).json()["id"]
    client.post("/v1/users/kid1/lock", headers=auth)

    body = client.get("/v1/system/snapshot", headers=auth).json()
    assert len(body["user_locks"]) == 1
    lock = body["user_locks"][0]
    assert lock["user_id"] == user_id
    assert lock["manual_lock"] is True
    assert lock["set_by"] == "operator"


def test_snapshot_includes_agents_when_present(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    """Agents have no install endpoint yet; insert one directly to verify shape."""
    client.post("/v1/devices", json={"slug": "rig", "type": "pc", "os": "windows"}, headers=auth)
    with Session(_engine(configured_db)) as s:
        device = s.exec(select(Device).where(Device.slug == "rig")).one()
        s.add(Agent(device_id=device.id, type="windows-agent", config={}))
        s.commit()

    body = client.get("/v1/system/snapshot", headers=auth).json()
    assert len(body["agents"]) == 1
    agent = body["agents"][0]
    assert agent["type"] == "windows-agent"
    assert agent["last_heartbeat"] is None


# --- excluded tables (counts only) --------------------------------------------


def test_audit_log_count_increases_with_writes(client: TestClient, auth: dict[str, str]) -> None:
    """audit_log rows are NOT dumped; only the count is exposed."""
    initial = client.get("/v1/system/snapshot", headers=auth).json()["counts"]["audit_log"]
    assert initial == 0

    client.post("/v1/users", json={"username": "kid1"}, headers=auth)
    client.patch("/v1/settings", json={"agent_tick_seconds": 30}, headers=auth)

    body = client.get("/v1/system/snapshot", headers=auth).json()
    assert body["counts"]["audit_log"] >= 2
    # And nothing leaked into a top-level key:
    assert "audit_log" not in body


def test_agent_tokens_count_only(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.post("/v1/devices", json={"slug": "rig", "type": "pc", "os": "windows"}, headers=auth)
    with Session(_engine(configured_db)) as s:
        device = s.exec(select(Device).where(Device.slug == "rig")).one()
        s.add(AgentToken(token_hash="abc123", device_id=device.id))
        s.commit()

    body = client.get("/v1/system/snapshot", headers=auth).json()
    assert body["counts"]["agent_tokens"] == 1
    assert "agent_tokens" not in body


# --- auth ---------------------------------------------------------------------


def test_snapshot_unauthenticated(client: TestClient) -> None:
    assert client.get("/v1/system/snapshot").status_code == 401
