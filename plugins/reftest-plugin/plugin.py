"""Reference test plugin (step 10 — no-op).

This plugin exists to prove the discovery contract end-to-end (the kernel
finds it, parses ``manifest.toml``, imports this module, picks
``ReftestPlugin`` as the leaf class, validates the empty ``Config``).

Step 11 will replace ``reconcile`` with logic that writes a sentinel file
when ``scope.locked`` is true and clears it when false — that's what the
kernel acceptance test asserts against. For now the kernel only needs to
prove that *discovery* works; ``reconcile`` returning ``ok()`` is enough.
"""

from __future__ import annotations

from curfew.plugin import Plugin, ReconcileResult, Scope
from pydantic import BaseModel


class Config(BaseModel):
    """Empty config — the reftest plugin takes no operator input.

    Step 11 will add a ``sentinel_path`` field so each test can point the
    plugin at its own ``tmp_path``.
    """


class ReftestPlugin(Plugin):
    def __init__(self, config: Config) -> None:
        self.config = config

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        return ReconcileResult.ok()
