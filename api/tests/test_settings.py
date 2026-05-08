"""Tests for /v1/settings (singleton runtime tunables)."""

from __future__ import annotations

from pathlib import Path

from curfew.models import AuditLog, AuditTargetKind
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select


def _engine(db: Path):
    return create_engine(f"sqlite:///{db}")


# --- get ----------------------------------------------------------------------


def test_get_returns_seeded_defaults(client: TestClient, auth: dict[str, str]) -> None:
    r = client.get("/v1/settings", headers=auth)
    assert r.status_code == 200
    assert r.json() == {
        "agent_tick_seconds": 60,
        "manifest_tick_seconds": 3600,
        "plugin_resync_seconds": 300,
        "plugin_reconcile_timeout_seconds": 30,
        "audit_retention_days": 90,
    }


def test_get_unauthenticated(client: TestClient) -> None:
    assert client.get("/v1/settings").status_code == 401


# --- patch --------------------------------------------------------------------


def test_patch_single_field(client: TestClient, auth: dict[str, str]) -> None:
    r = client.patch("/v1/settings", json={"agent_tick_seconds": 30}, headers=auth)
    assert r.status_code == 200
    body = r.json()
    assert body["agent_tick_seconds"] == 30
    # Other fields untouched.
    assert body["manifest_tick_seconds"] == 3600
    assert body["plugin_resync_seconds"] == 300


def test_patch_multiple_fields(client: TestClient, auth: dict[str, str]) -> None:
    r = client.patch(
        "/v1/settings",
        json={"agent_tick_seconds": 15, "audit_retention_days": 30},
        headers=auth,
    )
    assert r.status_code == 200
    body = r.json()
    assert body["agent_tick_seconds"] == 15
    assert body["audit_retention_days"] == 30


def test_patch_persists_across_reads(client: TestClient, auth: dict[str, str]) -> None:
    client.patch("/v1/settings", json={"plugin_resync_seconds": 600}, headers=auth)
    body = client.get("/v1/settings", headers=auth).json()
    assert body["plugin_resync_seconds"] == 600


def test_patch_empty_body_is_noop(client: TestClient, auth: dict[str, str]) -> None:
    r = client.patch("/v1/settings", json={}, headers=auth)
    assert r.status_code == 200
    # Defaults still in place.
    assert r.json()["agent_tick_seconds"] == 60


def test_patch_zero_rejected(client: TestClient, auth: dict[str, str]) -> None:
    r = client.patch("/v1/settings", json={"agent_tick_seconds": 0}, headers=auth)
    assert r.status_code == 422


def test_patch_negative_rejected(client: TestClient, auth: dict[str, str]) -> None:
    r = client.patch("/v1/settings", json={"audit_retention_days": -1}, headers=auth)
    assert r.status_code == 422


def test_patch_unknown_field_ignored(client: TestClient, auth: dict[str, str]) -> None:
    """Pydantic's default is to ignore unknown keys; document the behavior."""
    r = client.patch("/v1/settings", json={"agent_tick_seconds": 45, "bogus": 1}, headers=auth)
    assert r.status_code == 200
    assert r.json()["agent_tick_seconds"] == 45


def test_patch_unauthenticated(client: TestClient) -> None:
    assert client.patch("/v1/settings", json={"agent_tick_seconds": 30}).status_code == 401


# --- audit --------------------------------------------------------------------


def test_patch_audits_changed_fields_only(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.patch(
        "/v1/settings",
        json={"agent_tick_seconds": 45, "plugin_resync_seconds": 120},
        headers=auth,
    )
    with Session(_engine(configured_db)) as s:
        rows = s.exec(
            select(AuditLog).where(AuditLog.target_kind == AuditTargetKind.SETTINGS)
        ).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.action == "settings.update"
    assert row.target_id == "singleton"
    assert row.payload == {"agent_tick_seconds": 45, "plugin_resync_seconds": 120}


def test_empty_patch_does_not_audit(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.patch("/v1/settings", json={}, headers=auth)
    with Session(_engine(configured_db)) as s:
        rows = s.exec(
            select(AuditLog).where(AuditLog.target_kind == AuditTargetKind.SETTINGS)
        ).all()
    assert rows == []
