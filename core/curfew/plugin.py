"""Plugin SDK — what plugin authors import.

Per ADR-005 + ADR-010 + PLUGINS.md: a plugin is a Python class subclassing
``Plugin`` from this module, with one ``reconcile(scope)`` method to override.
The kernel discovers it from a ``CURFEW_PLUGINS_DIRS`` entry, instantiates it
with operator-supplied config, and calls ``reconcile`` when the lock state
changes for a user the plugin governs (or on the safety-net resync — step 11).

This module deliberately exports a tiny surface:

- ``Plugin`` — the base class.
- ``Scope`` — what gets passed into ``reconcile``: which user, are they
  locked, and what reasons fired. ``target_apps`` is deliberately *not* in
  ``Scope`` yet; PLAN.md's per-rule user config (and target_apps with it) is
  deferred until the first concrete agent or plugin needs it. Adding fields
  to ``Scope`` later is non-breaking — existing plugins keep working since
  they just don't read the new fields.
- ``ReconcileResult`` — the return shape with ``ok()`` and ``error(msg)``
  helpers so the plugin author doesn't construct it directly.
- ``PluginManifest`` — what the loader parses from each plugin's
  ``manifest.toml``. Authors don't construct these; the loader does.

Pydantic isn't imported here on purpose — plugin authors bring their own
``Config`` Pydantic model in ``plugin.py``; the loader resolves the model
class by the manifest's ``config_schema`` name and validates assignment
configs against it.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum

from curfew.schemas import Reason


@dataclass(frozen=True, slots=True)
class Scope:
    """What ``Plugin.reconcile`` receives.

    Plugins always operate at user scope (the ``users`` column on
    ``plugin_assignments`` lists usernames or ``["*"]``). Device-scope
    rules — e.g. shared-device-lock — fan out to per-user scopes for
    plugin reconciliation; plugins don't see device scope directly.
    """

    user: str
    locked: bool
    reasons: list[Reason] = field(default_factory=list)


class ReconcileStatus(StrEnum):
    OK = "ok"
    ERROR = "error"


@dataclass(frozen=True, slots=True)
class ReconcileResult:
    """Return value of ``Plugin.reconcile``.

    Constructed via ``ReconcileResult.ok()`` or ``ReconcileResult.error(msg)``
    rather than directly so authors don't need to know about the enum.
    """

    status: ReconcileStatus
    message: str | None = None

    @classmethod
    def ok(cls) -> ReconcileResult:
        return cls(status=ReconcileStatus.OK)

    @classmethod
    def error(cls, message: str) -> ReconcileResult:
        return cls(status=ReconcileStatus.ERROR, message=message)

    @property
    def succeeded(self) -> bool:
        return self.status == ReconcileStatus.OK


@dataclass(frozen=True, slots=True)
class PluginManifest:
    """Parsed ``manifest.toml`` for one plugin folder.

    The loader builds these; plugin authors don't construct them directly.
    ``config_schema`` is the *name* of the Pydantic model class declared in
    ``plugin.py`` — the loader resolves it to the actual class after the
    module imports.
    """

    type: str
    name: str
    version: str
    description: str
    config_schema: str


class Plugin:
    """Plugin base class.

    Subclass and implement ``reconcile``. The kernel constructs your plugin
    by calling ``YourPlugin(config=<validated Config instance>)`` so your
    ``__init__`` should accept a single ``config`` argument typed as the
    Pydantic model named in the manifest's ``config_schema``.

    Discovery selects the **leaf** ``Plugin`` subclass in your module — so
    you can have internal helper base classes (``BaseAdGuardClient(Plugin)``
    plus ``AdGuardPlugin(BaseAdGuardClient)``) and the leaf wins.
    """

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        raise NotImplementedError(f"{type(self).__name__} must override Plugin.reconcile")
