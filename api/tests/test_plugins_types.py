"""Tests for ``GET /v1/plugins/types``.

Discovery itself is exhaustively covered in ``core/tests/test_plugin_loader.py``;
these tests focus on the endpoint shape and auth gate. The registry is built
once at startup, so we inject a hand-built one onto ``app.state`` instead of
spinning up real plugin folders.
"""

from __future__ import annotations

from pathlib import Path

from curfew.plugin import Plugin, PluginManifest
from curfew.plugin_loader import PluginRegistry, PluginType
from fastapi.testclient import TestClient
from pydantic import BaseModel


class _DemoConfig(BaseModel):
    pass


class _DemoPlugin(Plugin):
    pass


def _make_registry(*entries: PluginType) -> PluginRegistry:
    r = PluginRegistry()
    for e in entries:
        r.add(e)
    return r


def _ok_entry() -> PluginType:
    return PluginType(
        type="demo",
        folder=Path("/tmp/plugins/demo"),
        manifest=PluginManifest(
            type="demo",
            name="Demo plugin",
            version="1.2.3",
            description="A test plugin",
            config_schema="Config",
        ),
        plugin_class=_DemoPlugin,
        config_model=_DemoConfig,
        error=None,
    )


def _failed_entry() -> PluginType:
    return PluginType(
        type="broken",
        folder=Path("/tmp/plugins/broken"),
        manifest=None,
        plugin_class=None,
        config_model=None,
        error="manifest.toml: missing required field(s): config_schema",
        has_requirements_txt=True,
    )


def test_types_unauthenticated(client: TestClient) -> None:
    assert client.get("/v1/plugins/types").status_code == 401


def test_types_empty_registry(client: TestClient, auth: dict[str, str]) -> None:
    client.app.state.plugin_registry = _make_registry()
    r = client.get("/v1/plugins/types", headers=auth)
    assert r.status_code == 200
    assert r.json() == []


def test_types_lists_successful_plugin(client: TestClient, auth: dict[str, str]) -> None:
    client.app.state.plugin_registry = _make_registry(_ok_entry())
    r = client.get("/v1/plugins/types", headers=auth)
    assert r.status_code == 200
    [item] = r.json()
    assert item == {
        "type": "demo",
        "name": "Demo plugin",
        "version": "1.2.3",
        "description": "A test plugin",
        "config_schema": "Config",
        "error": None,
        "has_requirements_txt": False,
    }


def test_types_surfaces_failed_plugin(client: TestClient, auth: dict[str, str]) -> None:
    """A failed plugin appears with `error` populated and manifest fields null."""
    client.app.state.plugin_registry = _make_registry(_failed_entry())
    r = client.get("/v1/plugins/types", headers=auth)
    assert r.status_code == 200
    [item] = r.json()
    assert item["type"] == "broken"
    assert item["name"] is None
    assert item["error"] is not None
    assert "config_schema" in item["error"]
    assert item["has_requirements_txt"] is True


def test_types_sorted_by_type(client: TestClient, auth: dict[str, str]) -> None:
    """Listing is alphabetical by ``type`` so the operator's UI is stable."""
    a = PluginType(
        type="alpha",
        folder=Path("/tmp/a"),
        manifest=PluginManifest(
            type="alpha",
            name="A",
            version="1.0",
            description="A",
            config_schema="Config",
        ),
        plugin_class=_DemoPlugin,
        config_model=_DemoConfig,
        error=None,
    )
    b = PluginType(
        type="beta",
        folder=Path("/tmp/b"),
        manifest=PluginManifest(
            type="beta",
            name="B",
            version="1.0",
            description="B",
            config_schema="Config",
        ),
        plugin_class=_DemoPlugin,
        config_model=_DemoConfig,
        error=None,
    )
    # Add in reverse order; .all() sorts.
    client.app.state.plugin_registry = _make_registry(b, a)
    r = client.get("/v1/plugins/types", headers=auth)
    assert [p["type"] for p in r.json()] == ["alpha", "beta"]
