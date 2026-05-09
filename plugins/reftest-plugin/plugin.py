"""Reference test plugin — writes a sentinel file when the user is locked.

Used by the kernel acceptance test (PLAN.md §"Acceptance: kernel done"
steps 11-16) to prove the full plugin path works end-to-end:

- Discovery picks this folder up at startup.
- Operator assigns it with a ``sentinel_path`` config and a list of
  governed users.
- Operator locks a user; the lock route schedules
  ``runtime.dispatch_for_user`` as a background task; the kernel calls
  ``ReftestPlugin.reconcile`` with ``Scope(user, locked=True, ...)``;
  this method writes ``<sentinel_path>`` containing the username.
- Operator unlocks; reconcile fires again with ``locked=False``; this
  method removes the sentinel file.

Idempotent on both legs — locking a user already-locked rewrites the
file with the same content; unlocking when the file isn't there is a
no-op. That matches the safety-net resync semantics: reconcile may be
called multiple times for the same state; results should converge.
"""

from __future__ import annotations

from pathlib import Path

from curfew.plugin import Plugin, ReconcileResult, Scope
from pydantic import BaseModel


class Config(BaseModel):
    """Where to write the sentinels.

    Tests typically point ``sentinel_path`` at ``tmp_path / "sentinel"``;
    live smoke tests use something like ``/tmp/curfew-reftest-sentinel``.

    ``devices_sentinel_path`` is an opt-in second sentinel that records
    the comma-separated list of device slugs the kernel passed in
    ``Scope.devices``. Used by the kernel acceptance test for the
    ``Scope.devices`` extension; left ``None`` for the simpler tests
    that just assert lock state propagated.
    """

    sentinel_path: str
    devices_sentinel_path: str | None = None


class ReftestPlugin(Plugin):
    def __init__(self, config: Config) -> None:
        self.config = config

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        path = Path(self.config.sentinel_path)
        if scope.locked:
            path.write_text(scope.user)
        else:
            path.unlink(missing_ok=True)

        if self.config.devices_sentinel_path is not None:
            dpath = Path(self.config.devices_sentinel_path)
            if scope.locked:
                dpath.write_text(",".join(d.slug for d in scope.devices))
            else:
                dpath.unlink(missing_ok=True)

        return ReconcileResult.ok()
