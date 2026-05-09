"""Plugin runtime — instance lifecycle + reconcile dispatch + safety-net resync.

Per PLAN.md §"Plugin lifecycle" + ADR-005 + PLUGINS.md §"Lifecycle":

- The kernel **instantiates** assigned plugins at startup (and on assign/PATCH)
  using the discovered class + the operator's config. The instance lives in
  memory; its lifetime is the core's. There's no second persistent layer —
  ``PluginAssignment`` rows are the desired state, instances are the runtime
  projection.
- When a user's lock state changes, the kernel **dispatches** ``reconcile``
  on every plugin governing that user. Dispatch is async (FastAPI
  ``BackgroundTasks``) so the originating HTTP request returns as soon as the
  DB write commits — slow plugins don't delay the operator.
- A **safety-net resync** runs every ``plugin_resync_seconds`` and calls
  ``reconcile`` for every managed user, on every governing plugin. Catches
  missed events from a crash / restart between a state change and the
  matching reconcile.

Errors at the ``reconcile`` boundary are **caught + audited, never raised**.
The originating request has already returned by the time the background task
runs; raising would just kill the task. ``plugin.reconcile_failed`` audit
rows are how the operator confirms a specific reconcile actually ran (or
didn't). PLUGINS.md §"What can go wrong".
"""

from __future__ import annotations

import asyncio
import logging
from contextlib import contextmanager
from dataclasses import dataclass, replace
from typing import Any

from sqlalchemy import Engine
from sqlmodel import Session, select

from curfew.audit import record_audit
from curfew.models import AuditTargetKind, User
from curfew.models import Settings as SettingsRow
from curfew.plugin import Plugin, ReconcileResult, Scope
from curfew.plugin_loader import PluginRegistry
from curfew.rules import user_scope

logger = logging.getLogger(__name__)


class UnknownPluginTypeError(LookupError):
    """The runtime was asked to assign/update an instance whose ``type`` isn't discovered."""


class PluginInstantiationError(RuntimeError):
    """The plugin class raised during ``__init__`` (after config validation passed)."""


class AlreadyAssignedError(ValueError):
    """``(type, instance_id)`` is already in the runtime."""


class NotAssignedError(KeyError):
    """``(type, instance_id)`` is not in the runtime."""


@dataclass(frozen=True, slots=True)
class AssignedInstance:
    """The runtime's view of one assigned plugin instance.

    Frozen + slot-based for cheap copying via ``dataclasses.replace`` on
    PATCH. ``instance`` is the live ``Plugin`` object (callable
    ``reconcile``). ``config`` is the validated dict (round-tripped through
    the plugin's Pydantic config model).
    """

    type: str
    instance_id: str
    instance: Plugin
    config: dict[str, Any]
    users: list[str]
    enabled: bool


class PluginRuntime:
    """In-memory ``{(type, instance_id): AssignedInstance}`` map + dispatch.

    One process-global instance, stashed on ``app.state.plugin_runtime``.
    All mutations go through ``async`` methods guarded by ``_lock`` so
    concurrent operator actions can't tear the map mid-update.
    """

    def __init__(self, registry: PluginRegistry, engine: Engine) -> None:
        self._registry = registry
        self._engine = engine
        self._instances: dict[tuple[str, str], AssignedInstance] = {}
        self._lock = asyncio.Lock()

    # --- Read API ------------------------------------------------------------

    def all_instances(self) -> list[AssignedInstance]:
        """Return every instance, sorted by ``(type, instance_id)``.

        Named ``all_instances`` (not ``list``) to avoid shadowing the
        ``list`` builtin in mypy's view of method-parameter annotations
        like ``users: list[str]`` further down.
        """
        return sorted(self._instances.values(), key=lambda x: (x.type, x.instance_id))

    def get(self, type_name: str, instance_id: str) -> AssignedInstance | None:
        return self._instances.get((type_name, instance_id))

    # --- Lifecycle (mutations) -----------------------------------------------

    def build_instance(self, type_name: str, config: dict[str, Any]) -> Plugin:
        """Validate config + construct a ``Plugin``. Raises on either step.

        Public so the API layer can pre-validate config before writing the
        DB row, surfacing 422s without leaving an orphaned row behind.
        """
        ptype = self._registry.get(type_name)
        if ptype is None or ptype.error is not None:
            raise UnknownPluginTypeError(
                f"plugin type {type_name!r} is not discovered or failed to load"
            )
        assert ptype.config_model is not None
        assert ptype.plugin_class is not None
        try:
            validated = ptype.config_model(**config)
        except Exception:
            # Let pydantic.ValidationError propagate so callers can map it
            # to a 422 with field-level detail.
            raise
        try:
            # Plugin subclasses define their own __init__ taking the
            # plugin's Pydantic config; the base `Plugin` doesn't, so
            # mypy can't see the call signature.
            return ptype.plugin_class(validated)  # type: ignore[call-arg]
        except Exception as exc:
            raise PluginInstantiationError(f"plugin {type_name!r} __init__ raised: {exc}") from exc

    async def register(
        self,
        *,
        type_name: str,
        instance_id: str,
        instance: Plugin,
        config: dict[str, Any],
        users: list[str],
        enabled: bool = True,
    ) -> AssignedInstance:
        """Register a pre-built ``Plugin`` instance.

        Split from ``build_instance`` so the API can validate + construct
        first (failing fast with 422 / 500), write the DB row (failing
        with 409 on duplicate), and only then commit the runtime mutation
        — keeping the in-memory map and the ``plugin_assignments`` table
        in lock-step.
        """
        async with self._lock:
            key = (type_name, instance_id)
            if key in self._instances:
                raise AlreadyAssignedError(f"{type_name}/{instance_id}")
            live = AssignedInstance(
                type=type_name,
                instance_id=instance_id,
                instance=instance,
                config=config,
                users=list(users),
                enabled=enabled,
            )
            self._instances[key] = live
            return live

    async def unassign(self, *, type_name: str, instance_id: str) -> None:
        """Drop the instance from the map. Idempotent — silent if absent."""
        async with self._lock:
            self._instances.pop((type_name, instance_id), None)

    async def update(
        self,
        *,
        type_name: str,
        instance_id: str,
        config: dict[str, Any] | None = None,
        users: list[str] | None = None,
        enabled: bool | None = None,
    ) -> AssignedInstance:
        """Mutate one or more fields. Re-instantiates iff ``config`` changed."""
        async with self._lock:
            key = (type_name, instance_id)
            live = self._instances.get(key)
            if live is None:
                raise NotAssignedError(f"{type_name}/{instance_id}")

            if config is not None and config != live.config:
                # PLAN.md option 1: re-instantiate. Cheaper than asking every
                # plugin author to handle live config swaps inside reconcile.
                new_instance = self.build_instance(type_name, config)
                live = replace(live, instance=new_instance, config=config)
            if users is not None:
                live = replace(live, users=list(users))
            if enabled is not None:
                live = replace(live, enabled=enabled)

            self._instances[key] = live
            return live

    # --- Dispatch ------------------------------------------------------------

    async def dispatch_for_user(self, username: str) -> None:
        """Reconcile every plugin governing ``username``.

        Runs reconciles concurrently. ``_run_one`` catches its own errors,
        so ``gather`` never sees one. Builds the ``Scope`` by evaluating the
        rule pipeline against a fresh session — the originating HTTP
        session is closed by the time a background task runs.
        """
        governing = [
            live
            for live in self._instances.values()
            if live.enabled and self._governs(live, username)
        ]
        if not governing:
            return

        with self._session() as session:
            status = user_scope.evaluate(session, username)
        scope = Scope(user=username, locked=status.locked, reasons=list(status.reasons))

        await asyncio.gather(*[self._run_one(live, scope) for live in governing])

    async def safety_net_resync(self) -> None:
        """Walk every managed user, dispatch.

        PLAN.md §"Plugin lifecycle" point 4: the slow tick that catches
        missed events from a restart. Reading the timeout fresh each
        invocation isn't enough — the *interval* is read by the lifespan
        loop, not here.
        """
        with self._session() as session:
            # Filter in Python rather than SQL: SQLModel + mypy can't agree on
            # what `User.managed` is typed as for a boolean WHERE clause, and
            # the user table is small enough at homelab scale that the in-SQL
            # filter saves nothing.
            usernames = [u.username for u in session.exec(select(User)).all() if u.managed]
        for username in usernames:
            try:
                await self.dispatch_for_user(username)
            except asyncio.CancelledError:
                raise
            except Exception:
                # ``_run_one`` already catches per-reconcile errors; the only
                # thing that should escape here is a programming bug. Log
                # and keep iterating so one bad user doesn't stall the loop.
                logger.exception("safety-net resync failed for %r", username)

    # --- Internals -----------------------------------------------------------

    @staticmethod
    def _governs(live: AssignedInstance, username: str) -> bool:
        return "*" in live.users or username in live.users

    async def _run_one(self, live: AssignedInstance, scope: Scope) -> None:
        """Call one plugin's ``reconcile`` with a timeout; audit failures."""
        timeout = self._read_timeout()
        try:
            result: ReconcileResult = await asyncio.wait_for(
                live.instance.reconcile(scope), timeout=timeout
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            self._audit_failure(live, scope, error=f"timed out after {timeout}s")
            return
        except Exception as exc:
            self._audit_failure(live, scope, error=f"{type(exc).__name__}: {exc}")
            return
        if not result.succeeded:
            self._audit_failure(live, scope, error=result.message or "reconcile returned error")

    def _audit_failure(self, live: AssignedInstance, scope: Scope, *, error: str) -> None:
        try:
            with self._session() as session:
                record_audit(
                    session,
                    actor="system",
                    action="plugin.reconcile_failed",
                    target_kind=AuditTargetKind.PLUGIN,
                    target_id=f"{live.type}/{live.instance_id}",
                    payload={
                        "user": scope.user,
                        "locked": scope.locked,
                        "error": error,
                    },
                )
                session.commit()
        except Exception:
            # Audit is best-effort; we already lost the reconcile, don't
            # also lose the audit chain by raising here.
            logger.exception(
                "failed to audit plugin.reconcile_failed for %s/%s",
                live.type,
                live.instance_id,
            )

    def _read_timeout(self) -> int:
        with self._session() as session:
            row = session.exec(select(SettingsRow)).one()
            return int(row.plugin_reconcile_timeout_seconds)

    @contextmanager
    def _session(self):  # type: ignore[no-untyped-def]
        with Session(self._engine) as s:
            yield s
