"""Tests for /v1/apps CRUD."""

from __future__ import annotations

from pathlib import Path

from curfew.models import AuditLog, AuditTargetKind
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select


def _engine(db: Path):
    return create_engine(f"sqlite:///{db}")


# --- create -------------------------------------------------------------------


def test_create_minimal(client: TestClient, auth: dict[str, str]) -> None:
    r = client.post("/v1/apps", json={"slug": "steam"}, headers=auth)
    assert r.status_code == 201
    body = r.json()
    assert body["slug"] == "steam"
    assert body["exe_paths"] == []
    assert body["process_names"] == []
    assert body["urls"] == []


def test_create_full(client: TestClient, auth: dict[str, str]) -> None:
    r = client.post(
        "/v1/apps",
        json={
            "slug": "steam",
            "exe_paths": ["C:/Steam/steam.exe"],
            "process_names": ["steam.exe"],
            "urls": ["https://store.steampowered.com"],
        },
        headers=auth,
    )
    assert r.status_code == 201
    assert r.json()["exe_paths"] == ["C:/Steam/steam.exe"]


def test_create_duplicate_slug_409(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/apps", json={"slug": "steam"}, headers=auth)
    r = client.post("/v1/apps", json={"slug": "steam"}, headers=auth)
    assert r.status_code == 409


def test_create_unauthenticated(client: TestClient) -> None:
    assert client.post("/v1/apps", json={"slug": "steam"}).status_code == 401


# --- list / get ---------------------------------------------------------------


def test_list_alphabetical(client: TestClient, auth: dict[str, str]) -> None:
    for slug in ["zoo", "alpha", "steam"]:
        client.post("/v1/apps", json={"slug": slug}, headers=auth)
    r = client.get("/v1/apps", headers=auth)
    assert [a["slug"] for a in r.json()] == ["alpha", "steam", "zoo"]


def test_get_missing_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.get("/v1/apps/ghost", headers=auth).status_code == 404


# --- patch --------------------------------------------------------------------


def test_patch_replace_lists(client: TestClient, auth: dict[str, str]) -> None:
    client.post(
        "/v1/apps",
        json={"slug": "steam", "exe_paths": ["old.exe"]},
        headers=auth,
    )
    r = client.patch(
        "/v1/apps/steam",
        json={"exe_paths": ["new.exe", "newer.exe"]},
        headers=auth,
    )
    assert r.json()["exe_paths"] == ["new.exe", "newer.exe"]


def test_patch_slug_rename(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/apps", json={"slug": "steam"}, headers=auth)
    r = client.patch("/v1/apps/steam", json={"slug": "valve-steam"}, headers=auth)
    assert r.status_code == 200
    assert r.json()["slug"] == "valve-steam"
    assert client.get("/v1/apps/steam", headers=auth).status_code == 404


def test_patch_empty_body_is_noop(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/apps", json={"slug": "steam"}, headers=auth)
    r = client.patch("/v1/apps/steam", json={}, headers=auth)
    assert r.status_code == 200
    assert r.json()["slug"] == "steam"


def test_patch_slug_conflict_409(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/apps", json={"slug": "a"}, headers=auth)
    client.post("/v1/apps", json={"slug": "b"}, headers=auth)
    r = client.patch("/v1/apps/a", json={"slug": "b"}, headers=auth)
    assert r.status_code == 409


def test_patch_missing_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.patch("/v1/apps/ghost", json={"slug": "x"}, headers=auth).status_code == 404


# --- delete -------------------------------------------------------------------


def test_delete_returns_204(client: TestClient, auth: dict[str, str]) -> None:
    client.post("/v1/apps", json={"slug": "steam"}, headers=auth)
    assert client.delete("/v1/apps/steam", headers=auth).status_code == 204
    assert client.get("/v1/apps/steam", headers=auth).status_code == 404


def test_delete_missing_404(client: TestClient, auth: dict[str, str]) -> None:
    assert client.delete("/v1/apps/ghost", headers=auth).status_code == 404


# --- audit --------------------------------------------------------------------


def test_audit_records_create_update_delete(
    client: TestClient, configured_db: Path, auth: dict[str, str]
) -> None:
    client.post("/v1/apps", json={"slug": "steam"}, headers=auth)
    client.patch("/v1/apps/steam", json={"urls": ["https://store.steampowered.com"]}, headers=auth)
    client.delete("/v1/apps/steam", headers=auth)
    with Session(_engine(configured_db)) as s:
        rows = s.exec(
            select(AuditLog)
            .where(AuditLog.target_kind == AuditTargetKind.APP)
            .order_by(AuditLog.id)  # type: ignore[arg-type]
        ).all()
    assert [r.action for r in rows] == ["app.create", "app.update", "app.delete"]
    update_payload = rows[1].payload
    assert update_payload == {"urls": ["https://store.steampowered.com"]}
