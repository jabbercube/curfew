"""Plugin discovery — walk ``CURFEW_PLUGINS_DIRS``, build the registry.

Per ADR-013 + PLUGINS.md: each subdirectory of each entry in
``CURFEW_PLUGINS_DIRS`` (colon-separated, PATH-style) is one plugin type.
Each must contain ``manifest.toml`` and ``plugin.py``; ``requirements.txt``
is optional.

This module owns the *what is available* side of plugins. The *what is
assigned* side (instances built from ``plugin_assignments`` rows + reconcile
dispatch + safety-net resync) lands in step 11.

Errors are contained per plugin: a malformed plugin folder is skipped with
its error captured on the registry entry — startup proceeds, other plugins
load, and the failed entry is visible at ``GET /v1/plugins/types``. This is
non-negotiable for a homelab where one operator-authored plugin shouldn't
take down the whole core (PLUGINS.md §"What can go wrong").

``requirements.txt`` is **not** auto-installed in this slice. If a plugin
needs deps, they have to be present in curfew-core's environment (via the
image build or a manual ``pip install``); otherwise the import will fail
naturally with a clear ``ModuleNotFoundError`` and the plugin will be
skipped with that error surfaced. Auto-install is a future tightening.
"""

from __future__ import annotations

import importlib.util
import inspect
import logging
import sys
import tomllib
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

from pydantic import BaseModel

from curfew.plugin import Plugin, PluginManifest

logger = logging.getLogger(__name__)

REQUIRED_MANIFEST_FIELDS = ("type", "name", "version", "description", "config_schema")

# Module-level aliases so the dataclass body's ``type:`` field doesn't
# shadow the builtin ``type[...]`` generic used a few lines below it.
_PluginClass = type[Plugin]
_ConfigModel = type[BaseModel]


@dataclass(frozen=True, slots=True)
class PluginType:
    """One entry in the discovery registry — successful or failed.

    On success, ``manifest``, ``plugin_class``, and ``config_model`` are all
    set; ``error`` is ``None``. On failure, ``error`` is a human-readable
    string and the rest may be partially populated (whichever stages got
    further before the error). The plugin's ``type`` field comes from the
    manifest when one was loaded, or falls back to the folder name when the
    manifest itself failed to parse — either way, the registry entry is
    addressable so the operator can see it in ``/v1/plugins/types``.
    """

    type: str
    folder: Path
    manifest: PluginManifest | None
    plugin_class: _PluginClass | None
    config_model: _ConfigModel | None
    error: str | None
    has_requirements_txt: bool = False


class PluginRegistry:
    """In-memory map of ``{type: PluginType}``.

    Built once at startup by ``discover_plugins`` and stashed on
    ``app.state.plugin_registry``. Step 11 will add a runtime layer on top
    that holds *instantiated* plugins per assignment row.
    """

    def __init__(self) -> None:
        self._types: dict[str, PluginType] = {}

    def add(self, ptype: PluginType) -> None:
        """Insert or replace; later-wins on duplicate ``type``.

        Per ADR-013: if the same ``type`` is declared in multiple
        ``CURFEW_PLUGINS_DIRS`` entries, later entries override earlier
        ones.
        """
        if ptype.type in self._types:
            logger.info(
                "plugin type %r overridden by entry in %s (previous: %s)",
                ptype.type,
                ptype.folder,
                self._types[ptype.type].folder,
            )
        self._types[ptype.type] = ptype

    def get(self, type_name: str) -> PluginType | None:
        return self._types.get(type_name)

    def all(self) -> list[PluginType]:
        return sorted(self._types.values(), key=lambda p: p.type)


def discover_plugins(plugins_dirs: str) -> PluginRegistry:
    """Walk colon-separated ``plugins_dirs`` and return the registry.

    Each existing directory is scanned in order; each direct subdirectory is
    treated as one plugin. Non-directory entries (files, symlinks pointing
    at non-dirs) are ignored. Missing ``CURFEW_PLUGINS_DIRS`` entries are
    silently skipped — operators commonly extend the var with paths that
    don't exist on every host (e.g. a dev path that's only present in some
    checkouts).
    """
    registry = PluginRegistry()
    for raw_path in plugins_dirs.split(":"):
        if not raw_path:
            continue
        root = Path(raw_path)
        if not root.is_dir():
            logger.debug("plugins dir %s does not exist; skipping", root)
            continue
        for entry in sorted(root.iterdir()):
            if not entry.is_dir():
                continue
            ptype = _load_plugin_folder(entry)
            if ptype.error is not None:
                logger.warning(
                    "plugin %r in %s failed to load: %s",
                    ptype.type,
                    ptype.folder,
                    ptype.error,
                )
            registry.add(ptype)
    return registry


def _load_plugin_folder(folder: Path) -> PluginType:
    """Load one plugin folder; capture errors instead of raising."""
    fallback_type = folder.name
    has_reqs = (folder / "requirements.txt").is_file()

    try:
        manifest = _read_manifest(folder)
    except Exception as exc:
        return PluginType(
            type=fallback_type,
            folder=folder,
            manifest=None,
            plugin_class=None,
            config_model=None,
            error=f"manifest.toml: {exc}",
            has_requirements_txt=has_reqs,
        )

    if has_reqs:
        logger.info(
            "plugin %r has requirements.txt; auto-install is not implemented "
            "in this slice — ensure deps are pre-installed in curfew-core's "
            "environment",
            manifest.type,
        )

    try:
        module = _import_plugin_module(folder, manifest.type)
    except Exception as exc:
        return PluginType(
            type=manifest.type,
            folder=folder,
            manifest=manifest,
            plugin_class=None,
            config_model=None,
            error=f"plugin.py: {exc}",
            has_requirements_txt=has_reqs,
        )

    try:
        plugin_class = _select_leaf_plugin_class(module)
    except _DiscoveryError as exc:
        return PluginType(
            type=manifest.type,
            folder=folder,
            manifest=manifest,
            plugin_class=None,
            config_model=None,
            error=str(exc),
            has_requirements_txt=has_reqs,
        )

    try:
        config_model = _resolve_config_model(module, manifest.config_schema)
    except _DiscoveryError as exc:
        return PluginType(
            type=manifest.type,
            folder=folder,
            manifest=manifest,
            plugin_class=plugin_class,
            config_model=None,
            error=str(exc),
            has_requirements_txt=has_reqs,
        )

    return PluginType(
        type=manifest.type,
        folder=folder,
        manifest=manifest,
        plugin_class=plugin_class,
        config_model=config_model,
        error=None,
        has_requirements_txt=has_reqs,
    )


def _read_manifest(folder: Path) -> PluginManifest:
    manifest_path = folder / "manifest.toml"
    if not manifest_path.is_file():
        raise FileNotFoundError("manifest.toml not found")
    with manifest_path.open("rb") as fp:
        data: dict[str, Any] = tomllib.load(fp)

    missing = [f for f in REQUIRED_MANIFEST_FIELDS if f not in data]
    if missing:
        raise ValueError(f"missing required field(s): {', '.join(missing)}")

    for field_name in REQUIRED_MANIFEST_FIELDS:
        if not isinstance(data[field_name], str):
            raise ValueError(f"field {field_name!r} must be a string")
        if not data[field_name]:
            raise ValueError(f"field {field_name!r} must not be empty")

    return PluginManifest(
        type=data["type"],
        name=data["name"],
        version=data["version"],
        description=data["description"],
        config_schema=data["config_schema"],
    )


def _import_plugin_module(folder: Path, type_name: str) -> ModuleType:
    """Import ``<folder>/plugin.py`` under a synthetic module name.

    The module is registered in ``sys.modules`` so plugin code that does
    ``from <its_own_module> import X`` works, but the synthetic name keeps
    plugin internals out of the global namespace and avoids collisions
    between different plugins' helper modules.
    """
    plugin_py = folder / "plugin.py"
    if not plugin_py.is_file():
        raise FileNotFoundError("plugin.py not found")

    module_name = f"curfew_plugin_{type_name}"
    spec = importlib.util.spec_from_file_location(module_name, plugin_py)
    if spec is None or spec.loader is None:
        raise ImportError(f"could not build spec for {plugin_py}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    try:
        spec.loader.exec_module(module)
    except Exception:
        # Don't leave a half-initialised module behind.
        sys.modules.pop(module_name, None)
        raise
    return module


class _DiscoveryError(Exception):
    """Internal — converted to ``PluginType.error`` at the call site."""


def _select_leaf_plugin_class(module: ModuleType) -> type[Plugin]:
    """Return the leaf ``Plugin`` subclass defined in ``module``.

    "Leaf" = a subclass that no other subclass in the same module extends.
    So ``BaseAdGuardClient(Plugin)`` + ``AdGuardPlugin(BaseAdGuardClient)``
    yields ``AdGuardPlugin`` (the leaf). Filtering by ``cls.__module__ ==
    module.__name__`` excludes ``Plugin`` itself (imported, not defined
    here) and any other imported subclasses.
    """
    candidates: list[type[Plugin]] = []
    for _, obj in inspect.getmembers(module, inspect.isclass):
        if obj is Plugin:
            continue
        if obj.__module__ != module.__name__:
            continue
        if not issubclass(obj, Plugin):
            continue
        candidates.append(obj)

    if not candidates:
        raise _DiscoveryError("no Plugin subclass found in plugin.py")

    leaves = [c for c in candidates if not _has_subclass_in(c, candidates)]
    if len(leaves) == 0:
        # Cycle or self-extending base — shouldn't happen for valid Python,
        # but bail explicitly rather than silently picking one.
        raise _DiscoveryError("Plugin subclass hierarchy has no leaf")
    if len(leaves) > 1:
        names = sorted(c.__name__ for c in leaves)
        raise _DiscoveryError(
            f"plugin.py declares multiple leaf Plugin subclasses ({', '.join(names)}); "
            "exactly one is required"
        )
    return leaves[0]


def _has_subclass_in(cls: type, candidates: list[type[Plugin]]) -> bool:
    return any(other is not cls and issubclass(other, cls) for other in candidates)


def _resolve_config_model(module: ModuleType, name: str) -> type[BaseModel]:
    """Look up ``manifest.config_schema`` as a Pydantic model in the module."""
    obj = getattr(module, name, None)
    if obj is None:
        raise _DiscoveryError(f"config_schema {name!r} not found in plugin.py")
    if not (inspect.isclass(obj) and issubclass(obj, BaseModel)):
        raise _DiscoveryError(f"config_schema {name!r} must be a pydantic.BaseModel subclass")
    return obj
