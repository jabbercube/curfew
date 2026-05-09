"""Tests for the plugin SDK surface that authors consume.

Doesn't exercise discovery (covered in test_plugin_loader.py) — just the
small set of types ``curfew.plugin`` exports.
"""

from __future__ import annotations

import pytest
from curfew.plugin import (
    Plugin,
    PluginManifest,
    ReconcileResult,
    ReconcileStatus,
    Scope,
)
from curfew.schemas import Reason


def test_scope_defaults_reasons_empty() -> None:
    s = Scope(user="kid1", locked=False)
    assert s.user == "kid1"
    assert s.locked is False
    assert s.reasons == []


def test_scope_carries_reasons() -> None:
    s = Scope(user="kid1", locked=True, reasons=[Reason(kind="manual_lock")])
    assert s.locked is True
    assert s.reasons[0].kind == "manual_lock"


def test_reconcile_result_ok() -> None:
    r = ReconcileResult.ok()
    assert r.status is ReconcileStatus.OK
    assert r.message is None
    assert r.succeeded is True


def test_reconcile_result_error() -> None:
    r = ReconcileResult.error("AdGuard returned 500")
    assert r.status is ReconcileStatus.ERROR
    assert r.message == "AdGuard returned 500"
    assert r.succeeded is False


def test_default_reconcile_raises_with_subclass_name() -> None:
    """A plugin author who forgets to override gets a clear error."""

    class HalfFinishedPlugin(Plugin):
        pass

    p = HalfFinishedPlugin()
    with pytest.raises(NotImplementedError, match="HalfFinishedPlugin"):
        import asyncio

        asyncio.run(p.reconcile(Scope(user="kid1", locked=True)))


def test_plugin_manifest_is_immutable() -> None:
    """Manifests come from disk and shouldn't be mutated at runtime."""
    m = PluginManifest(
        type="adguard",
        name="AdGuard",
        version="1.0.0",
        description="DNS sinkhole",
        config_schema="Config",
    )
    with pytest.raises((AttributeError, TypeError)):
        m.type = "other"  # type: ignore[misc]
