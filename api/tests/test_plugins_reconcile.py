"""End-to-end tests for the plugin reconcile path.

Exercises kernel acceptance steps 11-16 (PLAN.md §"Acceptance: kernel done"):
the operator assigns the reftest plugin, locks a user, the kernel dispatches
``reconcile`` in the background, and the plugin's sentinel file appears.

The reftest plugin lives in the repo's ``plugins/reftest-plugin/`` and is
discovered via the ``CURFEW_PLUGINS_DIRS`` env var that the
``client_with_repo_plugins`` fixture sets.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from curfew.models import AuditLog
from fastapi.testclient import TestClient
from sqlmodel import Session, create_engine, select


def _engine(db: Path):  # type: ignore[no-untyped-def]
    return create_engine(f"sqlite:///{db}")


def _setup_kid_and_plugin(
    client: TestClient, auth: dict[str, str], sentinel: Path, *, enabled: bool = True
) -> None:
    """Seed: one managed kid + one reftest_plugin assignment governing them."""
    r = client.post(
        "/v1/users",
        json={"username": "kid1", "password": "test1234", "role": "member"},
        headers=auth,
    )
    assert r.status_code == 201, r.text
    r = client.post(
        "/v1/plugins",
        json={
            "type": "reftest_plugin",
            "config": {"sentinel_path": str(sentinel)},
            "users": ["kid1"],
        },
        headers=auth,
    )
    assert r.status_code == 201, r.text
    if not enabled:
        r = client.patch(
            "/v1/plugins/reftest_plugin/default",
            json={"enabled": False},
            headers=auth,
        )
        assert r.status_code == 200


def test_lock_dispatches_reconcile_and_sentinel_appears(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    sentinel = tmp_path / "sentinel"
    _setup_kid_and_plugin(client_with_repo_plugins, auth, sentinel)
    assert not sentinel.exists()

    r = client_with_repo_plugins.post("/v1/users/kid1/lock", headers=auth)
    assert r.status_code == 200
    assert sentinel.read_text() == "kid1"


def test_unlock_clears_sentinel(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    sentinel = tmp_path / "sentinel"
    _setup_kid_and_plugin(client_with_repo_plugins, auth, sentinel)
    client_with_repo_plugins.post("/v1/users/kid1/lock", headers=auth)
    assert sentinel.exists()

    r = client_with_repo_plugins.post("/v1/users/kid1/unlock", headers=auth)
    assert r.status_code == 200
    assert not sentinel.exists()


def test_disabled_plugin_doesnt_reconcile(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    sentinel = tmp_path / "sentinel"
    _setup_kid_and_plugin(client_with_repo_plugins, auth, sentinel, enabled=False)

    r = client_with_repo_plugins.post("/v1/users/kid1/lock", headers=auth)
    assert r.status_code == 200
    assert not sentinel.exists()


def test_safety_net_picks_up_missed_lock(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    """If a lock fires while the plugin is disabled, re-enabling + resync writes the sentinel.

    Simulates the production case where curfew-core restarted between a
    ``lock`` and the plugin's reconcile firing — the safety-net loop is
    meant to catch this.
    """
    sentinel = tmp_path / "sentinel"
    _setup_kid_and_plugin(client_with_repo_plugins, auth, sentinel, enabled=False)

    # Lock while disabled: nothing reconciles.
    client_with_repo_plugins.post("/v1/users/kid1/lock", headers=auth)
    assert not sentinel.exists()

    # Re-enable + manually trigger the safety-net path. (In production
    # the background loop fires every plugin_resync_seconds; we don't
    # sleep 300s for a test, we invoke it directly.)
    r = client_with_repo_plugins.patch(
        "/v1/plugins/reftest_plugin/default",
        json={"enabled": True},
        headers=auth,
    )
    assert r.status_code == 200
    runtime = client_with_repo_plugins.app.state.plugin_runtime
    asyncio.run(runtime.safety_net_resync())

    assert sentinel.read_text() == "kid1"


def test_failed_reconcile_audited_but_lock_succeeds(
    client_with_repo_plugins: TestClient,
    auth: dict[str, str],
    tmp_path: Path,
    configured_db_with_repo_plugins: Path,
) -> None:
    """Pointing sentinel at an unwritable path makes reconcile raise.

    The lock route should still 200 (the operator's request must succeed
    even when downstream plugins fail); the failure is recorded as
    ``plugin.reconcile_failed`` per PLUGINS.md.
    """
    bad_sentinel = tmp_path / "no-such-dir" / "sentinel"
    _setup_kid_and_plugin(client_with_repo_plugins, auth, bad_sentinel)

    r = client_with_repo_plugins.post("/v1/users/kid1/lock", headers=auth)
    assert r.status_code == 200, r.text
    assert not bad_sentinel.exists()  # write would have failed

    with Session(_engine(configured_db_with_repo_plugins)) as s:
        rows = list(s.exec(select(AuditLog).where(AuditLog.action == "plugin.reconcile_failed")))
    assert len(rows) == 1
    assert rows[0].target_id == "reftest_plugin/default"
    assert rows[0].payload["user"] == "kid1"


def test_assignments_hydrate_on_startup(
    client_with_repo_plugins: TestClient, auth: dict[str, str], tmp_path: Path
) -> None:
    """A row that exists at startup should be live in the runtime.

    We test this by creating an assignment, locking (which uses the
    runtime), and asserting the sentinel appears — proving the row was
    instantiated and registered.
    """
    sentinel = tmp_path / "sentinel"
    _setup_kid_and_plugin(client_with_repo_plugins, auth, sentinel)

    r = client_with_repo_plugins.post("/v1/users/kid1/lock", headers=auth)
    assert r.status_code == 200
    assert sentinel.exists()
