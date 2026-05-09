"""Tests for plugin discovery — ``discover_plugins`` and ``PluginRegistry``.

The loader is the gatekeeper for whether a plugin folder produces a usable
type at startup. Almost every assertion here is "this kind of broken plugin
folder doesn't kill startup; it surfaces with a clear error" — that's the
non-negotiable contract from PLUGINS.md §"What can go wrong".
"""

from __future__ import annotations

from pathlib import Path
from textwrap import dedent

import pytest
from curfew.plugin import Plugin
from curfew.plugin_loader import discover_plugins
from pydantic import BaseModel


def _write_plugin(
    parent: Path,
    *,
    folder_name: str,
    manifest: str | None,
    plugin_py: str | None,
    requirements_txt: str | None = None,
) -> Path:
    """Build a synthetic plugin folder under ``parent``.

    ``None`` means "skip writing this file" — used to exercise missing-file
    error paths.
    """
    folder = parent / folder_name
    folder.mkdir(parents=True)
    if manifest is not None:
        (folder / "manifest.toml").write_text(manifest)
    if plugin_py is not None:
        (folder / "plugin.py").write_text(plugin_py)
    if requirements_txt is not None:
        (folder / "requirements.txt").write_text(requirements_txt)
    return folder


VALID_MANIFEST = dedent(
    """
    type = "demo"
    name = "Demo plugin"
    version = "1.0.0"
    description = "A test plugin"
    config_schema = "Config"
    """
).strip()

VALID_PLUGIN_PY = dedent(
    """
    from curfew.plugin import Plugin, ReconcileResult, Scope
    from pydantic import BaseModel

    class Config(BaseModel):
        url: str = "http://example.com"

    class DemoPlugin(Plugin):
        def __init__(self, config: Config):
            self.config = config

        async def reconcile(self, scope: Scope) -> ReconcileResult:
            return ReconcileResult.ok()
    """
).strip()


def test_empty_dirs_string_yields_empty_registry() -> None:
    registry = discover_plugins("")
    assert registry.all() == []


def test_missing_dir_silently_skipped(tmp_path: Path) -> None:
    """Operators may add paths that don't exist on every host."""
    nonexistent = tmp_path / "does-not-exist"
    registry = discover_plugins(str(nonexistent))
    assert registry.all() == []


def test_valid_plugin_loads(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py=VALID_PLUGIN_PY,
    )
    registry = discover_plugins(str(tmp_path))

    [ptype] = registry.all()
    assert ptype.type == "demo"
    assert ptype.error is None
    assert ptype.manifest is not None
    assert ptype.manifest.name == "Demo plugin"
    assert ptype.plugin_class is not None
    assert ptype.plugin_class.__name__ == "DemoPlugin"
    assert issubclass(ptype.plugin_class, Plugin)
    assert ptype.config_model is not None
    assert issubclass(ptype.config_model, BaseModel)


def test_missing_manifest_surfaces_error(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        folder_name="broken",
        manifest=None,
        plugin_py=VALID_PLUGIN_PY,
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.type == "broken"  # falls back to folder name
    assert ptype.error is not None
    assert "manifest.toml" in ptype.error
    assert ptype.manifest is None


def test_manifest_missing_required_field(tmp_path: Path) -> None:
    bad = dedent(
        """
        type = "demo"
        name = "Demo"
        version = "1.0.0"
        description = "..."
        """
    ).strip()  # missing config_schema
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=bad,
        plugin_py=VALID_PLUGIN_PY,
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is not None
    assert "config_schema" in ptype.error


def test_manifest_field_wrong_type(tmp_path: Path) -> None:
    bad = dedent(
        """
        type = "demo"
        name = "Demo"
        version = 100
        description = "..."
        config_schema = "Config"
        """
    ).strip()
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=bad,
        plugin_py=VALID_PLUGIN_PY,
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is not None
    assert "version" in ptype.error


def test_missing_plugin_py(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py=None,
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is not None
    assert "plugin.py" in ptype.error


def test_plugin_py_syntax_error(tmp_path: Path) -> None:
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py="this is not python {{{",
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is not None
    assert "plugin.py" in ptype.error


def test_plugin_py_no_plugin_subclass(tmp_path: Path) -> None:
    """Importing Plugin without subclassing it doesn't count."""
    body = dedent(
        """
        from curfew.plugin import Plugin
        from pydantic import BaseModel

        class Config(BaseModel):
            pass

        class NotAPlugin:
            pass
        """
    ).strip()
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py=body,
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is not None
    assert "no Plugin subclass" in ptype.error


def test_plugin_py_helper_base_then_leaf(tmp_path: Path) -> None:
    """A non-leaf base subclass + the actual leaf — leaf wins."""
    body = dedent(
        """
        from curfew.plugin import Plugin, ReconcileResult, Scope
        from pydantic import BaseModel

        class Config(BaseModel):
            pass

        class BaseHelper(Plugin):
            async def shared_helper(self) -> str:
                return "hi"

        class ActualPlugin(BaseHelper):
            async def reconcile(self, scope: Scope) -> ReconcileResult:
                return ReconcileResult.ok()
        """
    ).strip()
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py=body,
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is None
    assert ptype.plugin_class is not None
    assert ptype.plugin_class.__name__ == "ActualPlugin"


def test_plugin_py_multiple_leaves_is_an_error(tmp_path: Path) -> None:
    body = dedent(
        """
        from curfew.plugin import Plugin, ReconcileResult, Scope
        from pydantic import BaseModel

        class Config(BaseModel):
            pass

        class AlphaPlugin(Plugin):
            async def reconcile(self, scope: Scope) -> ReconcileResult:
                return ReconcileResult.ok()

        class BetaPlugin(Plugin):
            async def reconcile(self, scope: Scope) -> ReconcileResult:
                return ReconcileResult.ok()
        """
    ).strip()
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py=body,
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is not None
    assert "multiple leaf" in ptype.error
    # Both class names listed deterministically (sorted) so the operator
    # can see what to pick from.
    assert "AlphaPlugin" in ptype.error
    assert "BetaPlugin" in ptype.error


def test_config_schema_name_not_in_module(tmp_path: Path) -> None:
    body = dedent(
        """
        from curfew.plugin import Plugin, ReconcileResult, Scope

        class DemoPlugin(Plugin):
            async def reconcile(self, scope: Scope) -> ReconcileResult:
                return ReconcileResult.ok()
        """
    ).strip()  # no Config class
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py=body,
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is not None
    assert "Config" in ptype.error
    assert "not found" in ptype.error


def test_config_schema_not_a_pydantic_model(tmp_path: Path) -> None:
    body = dedent(
        """
        from curfew.plugin import Plugin, ReconcileResult, Scope

        class Config:  # NOT a BaseModel
            pass

        class DemoPlugin(Plugin):
            async def reconcile(self, scope: Scope) -> ReconcileResult:
                return ReconcileResult.ok()
        """
    ).strip()
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py=body,
    )
    [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is not None
    assert "BaseModel" in ptype.error


def test_requirements_txt_flagged_but_plugin_loads(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`requirements.txt` is recorded but doesn't block discovery."""
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py=VALID_PLUGIN_PY,
        requirements_txt="httpx>=0.25\n",
    )
    with caplog.at_level("INFO"):
        [ptype] = discover_plugins(str(tmp_path)).all()
    assert ptype.error is None
    assert ptype.has_requirements_txt is True
    assert any("requirements.txt" in r.message for r in caplog.records)


def test_override_on_duplicate_type_later_wins(tmp_path: Path) -> None:
    """Two dirs each declaring ``type = "demo"`` → the later dir's plugin wins."""
    first = tmp_path / "first"
    second = tmp_path / "second"
    _write_plugin(first, folder_name="demo", manifest=VALID_MANIFEST, plugin_py=VALID_PLUGIN_PY)

    second_py = VALID_PLUGIN_PY.replace("DemoPlugin", "DemoPluginV2")
    _write_plugin(second, folder_name="demo", manifest=VALID_MANIFEST, plugin_py=second_py)

    registry = discover_plugins(f"{first}:{second}")
    [ptype] = registry.all()
    assert ptype.plugin_class is not None
    assert ptype.plugin_class.__name__ == "DemoPluginV2"
    assert ptype.folder == second / "demo"


def test_multiple_dirs_both_contribute(tmp_path: Path) -> None:
    """Different ``type`` in each dir → both end up in the registry."""
    first = tmp_path / "first"
    second = tmp_path / "second"

    a_manifest = VALID_MANIFEST.replace('"demo"', '"alpha"')
    b_manifest = VALID_MANIFEST.replace('"demo"', '"beta"')
    _write_plugin(first, folder_name="alpha", manifest=a_manifest, plugin_py=VALID_PLUGIN_PY)
    _write_plugin(second, folder_name="beta", manifest=b_manifest, plugin_py=VALID_PLUGIN_PY)

    registry = discover_plugins(f"{first}:{second}")
    types = [p.type for p in registry.all()]
    assert types == ["alpha", "beta"]


def test_one_bad_plugin_doesnt_block_others(tmp_path: Path) -> None:
    """Per ADR-013: discovery survives per-plugin errors."""
    _write_plugin(
        tmp_path,
        folder_name="ok",
        manifest=VALID_MANIFEST.replace('"demo"', '"ok_one"'),
        plugin_py=VALID_PLUGIN_PY,
    )
    _write_plugin(
        tmp_path,
        folder_name="broken",
        manifest=None,
        plugin_py=VALID_PLUGIN_PY,
    )
    registry = discover_plugins(str(tmp_path))
    by_type = {p.type: p for p in registry.all()}
    assert by_type["ok_one"].error is None
    assert by_type["broken"].error is not None


def test_non_directory_entries_ignored(tmp_path: Path) -> None:
    """Stray files at the top level of a plugins dir don't crash the loader."""
    (tmp_path / "stray-file.txt").write_text("ignore me")
    _write_plugin(
        tmp_path,
        folder_name="demo",
        manifest=VALID_MANIFEST,
        plugin_py=VALID_PLUGIN_PY,
    )
    registry = discover_plugins(str(tmp_path))
    assert [p.type for p in registry.all()] == ["demo"]
