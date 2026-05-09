"""Tests for the AdGuard plugin's reconcile logic.

The plugin lives outside the curfew package (``plugins/adguard/plugin.py``)
and is normally loaded by the kernel's plugin discovery machinery. These
tests load it directly via ``importlib.util.spec_from_file_location`` so
they can exercise the class without running discovery.

HTTP is intercepted with ``httpx.MockTransport`` — no respx dep, no real
AdGuard required. Each test installs a per-test handler that records
POST bodies for assertions.
"""

from __future__ import annotations

import asyncio
import importlib.util
import json
from pathlib import Path
from typing import Any

import httpx
import pytest
from curfew.models import DeviceOS, DeviceType
from curfew.plugin import DeviceRef, Scope

# Load plugins/adguard/plugin.py without putting plugins/ on sys.path.
_PLUGIN_PATH = Path(__file__).resolve().parents[2] / "plugins" / "adguard" / "plugin.py"
_spec = importlib.util.spec_from_file_location("curfew_test_adguard_plugin", _PLUGIN_PATH)
assert _spec is not None and _spec.loader is not None
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
AdGuardPlugin = _module.AdGuardPlugin
Config = _module.Config


# --- Helpers -----------------------------------------------------------------


def _device(
    slug: str,
    mac: list[str],
    *,
    managed: bool = True,
    type_: DeviceType = DeviceType.PC,
    os_: DeviceOS = DeviceOS.WINDOWS,
) -> DeviceRef:
    return DeviceRef(slug=slug, type=type_, os=os_, mac=mac, managed=managed)


def _scope(*, locked: bool, devices: list[DeviceRef]) -> Scope:
    return Scope(user="kid1", locked=locked, reasons=[], devices=devices)


def _make_plugin(
    monkeypatch: pytest.MonkeyPatch,
    handler: Any,
    *,
    sinkhole: str = "0.0.0.0",
) -> Any:
    """Build the plugin and swap its httpx client for a mock-transport one."""
    monkeypatch.setenv("ADGUARD_TEST_PW", "secret")
    cfg = Config(
        url="http://adguard.test",
        username="admin",
        password_env="ADGUARD_TEST_PW",
        sinkhole_upstream=sinkhole,
    )
    p = AdGuardPlugin(cfg)
    p._client = httpx.AsyncClient(
        base_url="http://adguard.test",
        auth=(cfg.username, "secret"),
        transport=httpx.MockTransport(handler),
    )
    return p


def _ok_handler(
    initial_clients: list[dict[str, Any]],
    posts: list[dict[str, Any]],
) -> Any:
    """Mock transport handler: GET /clients returns ``initial_clients``;
    every POST is recorded into ``posts`` and answered with 200 ``{}``."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET" and request.url.path == "/control/clients":
            return httpx.Response(200, json={"clients": initial_clients})
        if request.method == "POST" and request.url.path.startswith("/control/clients/"):
            data = json.loads(request.content) if request.content else {}
            posts.append({"path": request.url.path, "data": data})
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"error": f"unmocked {request.method} {request.url.path}"})

    return handler


# --- Block leg ---------------------------------------------------------------


def test_block_creates_client_when_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[dict[str, Any]] = []
    p = _make_plugin(monkeypatch, _ok_handler([], posts))
    scope = _scope(locked=True, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert len(posts) == 1
    assert posts[0]["path"] == "/control/clients/add"
    assert posts[0]["data"]["name"] == "kid1-laptop"
    assert posts[0]["data"]["ids"] == ["aa:bb:cc:dd:ee:01"]
    assert posts[0]["data"]["upstreams"] == ["0.0.0.0"]
    assert posts[0]["data"]["filtering_enabled"] is False
    assert posts[0]["data"]["use_global_settings"] is True


def test_block_updates_existing_client_with_wrong_upstream(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts: list[dict[str, Any]] = []
    existing = {"name": "kid1-laptop", "ids": ["aa:bb:cc:dd:ee:01"], "upstreams": []}
    p = _make_plugin(monkeypatch, _ok_handler([existing], posts))
    scope = _scope(locked=True, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert len(posts) == 1
    assert posts[0]["path"] == "/control/clients/update"
    assert posts[0]["data"]["name"] == "kid1-laptop"
    assert posts[0]["data"]["data"]["upstreams"] == ["0.0.0.0"]


def test_block_idempotent_when_already_sinkholed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts: list[dict[str, Any]] = []
    existing = {
        "name": "kid1-laptop",
        "ids": ["aa:bb:cc:dd:ee:01"],
        "upstreams": ["0.0.0.0"],
    }
    p = _make_plugin(monkeypatch, _ok_handler([existing], posts))
    scope = _scope(locked=True, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert posts == []


def test_block_matches_existing_client_by_any_of_devices_macs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Device has two MACs; AdGuard knows it under the second one."""
    posts: list[dict[str, Any]] = []
    existing = {
        "name": "kid1-laptop",
        "ids": ["11:22:33:44:55:66"],
        "upstreams": [],
    }
    p = _make_plugin(monkeypatch, _ok_handler([existing], posts))
    scope = _scope(
        locked=True,
        devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01", "11:22:33:44:55:66"])],
    )

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    # Must update, not add — we found a match via the second MAC.
    assert len(posts) == 1
    assert posts[0]["path"] == "/control/clients/update"


def test_block_mac_lookup_is_case_insensitive(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[dict[str, Any]] = []
    # AdGuard returns uppercase; device declares lowercase. Should still match.
    existing = {
        "name": "kid1-laptop",
        "ids": ["AA:BB:CC:DD:EE:01"],
        "upstreams": [],
    }
    p = _make_plugin(monkeypatch, _ok_handler([existing], posts))
    scope = _scope(locked=True, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert len(posts) == 1
    assert posts[0]["path"] == "/control/clients/update"


def test_multi_mac_device_creates_one_client_with_all_macs(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    posts: list[dict[str, Any]] = []
    p = _make_plugin(monkeypatch, _ok_handler([], posts))
    scope = _scope(
        locked=True,
        devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01", "11:22:33:44:55:66"])],
    )

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert len(posts) == 1
    assert posts[0]["data"]["ids"] == ["aa:bb:cc:dd:ee:01", "11:22:33:44:55:66"]


# --- Unblock leg -------------------------------------------------------------


def test_unblock_clears_upstream(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[dict[str, Any]] = []
    existing = {
        "name": "kid1-laptop",
        "ids": ["aa:bb:cc:dd:ee:01"],
        "upstreams": ["0.0.0.0"],
    }
    p = _make_plugin(monkeypatch, _ok_handler([existing], posts))
    scope = _scope(locked=False, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert len(posts) == 1
    assert posts[0]["path"] == "/control/clients/update"
    assert posts[0]["data"]["data"]["upstreams"] == []


def test_unblock_noop_when_client_missing(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unblock leg never auto-creates: nothing to clear."""
    posts: list[dict[str, Any]] = []
    p = _make_plugin(monkeypatch, _ok_handler([], posts))
    scope = _scope(locked=False, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert posts == []


def test_unblock_noop_when_already_clear(monkeypatch: pytest.MonkeyPatch) -> None:
    posts: list[dict[str, Any]] = []
    existing = {"name": "kid1-laptop", "ids": ["aa:bb:cc:dd:ee:01"], "upstreams": []}
    p = _make_plugin(monkeypatch, _ok_handler([existing], posts))
    scope = _scope(locked=False, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert posts == []


def test_unblock_skips_clients_with_other_non_default_upstreams(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """If an AdGuard client has a non-sinkhole upstream we didn't set
    (e.g. a custom DNS the operator configured), the unblock leg leaves
    it alone — only sinkhole upstreams clear."""
    posts: list[dict[str, Any]] = []
    existing = {
        "name": "kid1-laptop",
        "ids": ["aa:bb:cc:dd:ee:01"],
        "upstreams": ["1.1.1.1"],
    }
    p = _make_plugin(monkeypatch, _ok_handler([existing], posts))
    scope = _scope(locked=False, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert posts == []


# --- Filtering ---------------------------------------------------------------


def test_devices_with_empty_mac_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """A device with no MAC isn't actionable in AdGuard. Skip silently."""
    posts: list[dict[str, Any]] = []
    p = _make_plugin(monkeypatch, _ok_handler([], posts))
    scope = _scope(locked=True, devices=[_device("kid1-phone", [])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    # No GET /clients either — short-circuit when no devices are actionable.
    assert posts == []


def test_unmanaged_devices_skipped(monkeypatch: pytest.MonkeyPatch) -> None:
    """Unmanaged devices in scope (e.g. shared TV) are left alone."""
    posts: list[dict[str, Any]] = []
    p = _make_plugin(monkeypatch, _ok_handler([], posts))
    scope = _scope(
        locked=True,
        devices=[_device("kid1-tv", ["aa:bb:cc:dd:ee:01"], managed=False)],
    )

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert posts == []


def test_no_devices_short_circuits_without_calling_adguard(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.url.path)
        return httpx.Response(200, json={"clients": []})

    p = _make_plugin(monkeypatch, handler)
    scope = _scope(locked=True, devices=[])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert seen == []


# --- Errors ------------------------------------------------------------------


def test_adguard_5xx_returns_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """AdGuard down → ReconcileResult.error (not raise). The runtime
    audits these as ``plugin.reconcile_failed``."""

    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(503, json={"error": "down"})

    p = _make_plugin(monkeypatch, handler)
    scope = _scope(locked=True, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert not result.succeeded
    assert result.message is not None
    assert "503" in result.message or "HTTPStatusError" in result.message


def test_post_failure_returns_error_result(monkeypatch: pytest.MonkeyPatch) -> None:
    """GET succeeds but the subsequent POST fails. Plugin still surfaces an error."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.method == "GET":
            return httpx.Response(200, json={"clients": []})
        return httpx.Response(500, json={"error": "boom"})

    p = _make_plugin(monkeypatch, handler)
    scope = _scope(locked=True, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert not result.succeeded


# --- Config ------------------------------------------------------------------


def test_custom_sinkhole_upstream_used(monkeypatch: pytest.MonkeyPatch) -> None:
    """Config-supplied sinkhole_upstream overrides the 0.0.0.0 default."""
    posts: list[dict[str, Any]] = []
    p = _make_plugin(monkeypatch, _ok_handler([], posts), sinkhole="127.0.0.1")
    scope = _scope(locked=True, devices=[_device("kid1-laptop", ["aa:bb:cc:dd:ee:01"])])

    result = asyncio.run(p.reconcile(scope))

    assert result.succeeded
    assert posts[0]["data"]["upstreams"] == ["127.0.0.1"]


def test_password_read_from_named_env_var(monkeypatch: pytest.MonkeyPatch) -> None:
    """The plugin reads the password from the env var named in config."""
    monkeypatch.setenv("MY_CUSTOM_VAR", "the-password")
    cfg = Config(
        url="http://adguard.test",
        username="admin",
        password_env="MY_CUSTOM_VAR",
    )
    p = AdGuardPlugin(cfg)
    # Internals: the AsyncClient was constructed with that password.
    assert p._client.auth is not None  # type: ignore[union-attr]


def test_unknown_password_env_var_raises_at_init(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Missing env var fails fast at __init__ — the runtime surfaces this
    as PluginInstantiationError on assign, with a 422-equivalent error."""
    monkeypatch.delenv("ADGUARD_NEVER_SET", raising=False)
    cfg = Config(
        url="http://adguard.test",
        username="admin",
        password_env="ADGUARD_NEVER_SET",
    )
    with pytest.raises(KeyError):
        AdGuardPlugin(cfg)
