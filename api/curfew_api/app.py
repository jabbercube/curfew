"""FastAPI app factory for curfew-api.

Builds the ``FastAPI`` instance, registers the kernel's lock-status rules,
mounts CORS, attaches the routers, and runs the lifespan that owns the
plugin runtime + safety-net resync loop. Auth is enforced via per-route
``Depends(require_actor)``, not as ASGI middleware: this keeps the OpenAPI
schema honest (the security requirement is documented on each protected
route) and lets ``/v1/health`` and ``/v1/openapi.json`` opt out by simply
not including the dependency.

Lifespan ownership (per ADR-005 + PLAN.md §"Plugin lifecycle"):

- Build the ``PluginRuntime``, hydrate it from ``plugin_assignments``
  rows. A row whose plugin type isn't discovered (folder removed) is
  logged and skipped — the row stays so the operator can decide.
- Start the safety-net resync task: every ``plugin_resync_seconds`` (read
  fresh each iteration so settings PATCHes take effect), call
  ``runtime.safety_net_resync()``.
- On shutdown: cancel the task and await it cleanly.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from curfew.config import get_config
from curfew.db import get_engine
from curfew.models import PluginAssignment
from curfew.models import Settings as SettingsRow
from curfew.plugin_loader import PluginRegistry, discover_plugins
from curfew.plugin_runtime import (
    PluginInstantiationError,
    PluginRuntime,
    UnknownPluginTypeError,
)
from curfew.rules import register_kernel_rules
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from pydantic import ValidationError
from sqlmodel import Session, select
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from curfew_api.routes import apps, auth, devices, health, locks, plugins, status, system, users
from curfew_api.routes.settings import router as settings_router

logger = logging.getLogger(__name__)


class SPAStaticFiles(StaticFiles):
    """``StaticFiles`` that falls back to ``index.html`` on missing paths.

    The bare ``StaticFiles(html=True)`` serves ``index.html`` for *directory*
    URLs (``/`` or ``/foo/``) but 404s on missing-file paths like ``/login``.
    A SPA's client-side router needs unknown paths to land on ``index.html``
    so it can route them in JS. This subclass catches the 404 and re-serves
    the SPA shell.
    """

    async def get_response(self, path: str, scope: Any) -> Response:
        try:
            return await super().get_response(path, scope)
        except StarletteHTTPException as exc:
            if exc.status_code == 404:
                return await super().get_response("index.html", scope)
            raise


async def _hydrate_runtime(runtime: PluginRuntime) -> None:
    """Instantiate every existing assignment row + register it.

    Errors are logged per-row and skipped — the row stays in the DB so
    the operator can fix the plugin folder or unassign explicitly. PLAN.md
    "graceful degradation": one missing plugin folder doesn't stop
    startup.
    """
    with Session(get_engine()) as session:
        rows = list(session.exec(select(PluginAssignment)).all())

    for row in rows:
        try:
            instance = runtime.build_instance(row.type, dict(row.config))
        except (UnknownPluginTypeError, ValidationError, PluginInstantiationError) as exc:
            logger.warning(
                "skipping assignment %s/%s on startup: %s",
                row.type,
                row.instance_id,
                exc,
            )
            continue
        try:
            await runtime.register(
                type_name=row.type,
                instance_id=row.instance_id,
                instance=instance,
                config=dict(row.config),
                users=list(row.users),
                paused=row.paused,
            )
        except Exception:
            logger.exception(
                "failed to register %s/%s during hydrate",
                row.type,
                row.instance_id,
            )


async def _safety_net_loop(runtime: PluginRuntime) -> None:
    """Periodic re-dispatch of every governed user.

    Reads ``plugin_resync_seconds`` from the singleton settings row each
    iteration so a settings PATCH that lowers the interval takes effect
    on the next tick. Sleeping is interruptible by cancellation (FastAPI
    cancels this task on shutdown).
    """
    while True:
        with Session(get_engine()) as session:
            interval = session.exec(select(SettingsRow)).one().plugin_resync_seconds
        try:
            await asyncio.sleep(interval)
        except asyncio.CancelledError:
            return
        try:
            await runtime.safety_net_resync()
        except asyncio.CancelledError:
            return
        except Exception:
            logger.exception("safety-net resync raised; loop continues")


@asynccontextmanager
async def lifespan(app: FastAPI) -> AsyncIterator[None]:
    """Owns the plugin runtime + safety-net loop for the app's lifetime."""
    config = get_config()
    registry: PluginRegistry = discover_plugins(config.plugins_dirs)
    app.state.plugin_registry = registry

    runtime = PluginRuntime(registry=registry, engine=get_engine())
    app.state.plugin_runtime = runtime
    await _hydrate_runtime(runtime)

    resync_task = asyncio.create_task(_safety_net_loop(runtime), name="plugin-safety-net")

    try:
        yield
    finally:
        resync_task.cancel()
        try:
            await resync_task
        except (asyncio.CancelledError, Exception):
            # Cancellation is the expected shutdown path; any other
            # exception was already logged inside the loop.
            pass


def create_app() -> FastAPI:
    """Build and return a configured FastAPI app."""
    register_kernel_rules()

    app = FastAPI(
        title="curfew-core",
        description="curfew API service",
        version="0.0.0",
        openapi_url="/v1/openapi.json",
        lifespan=lifespan,
    )

    config = get_config()
    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(auth.router)
    app.include_router(users.router)
    app.include_router(devices.router)
    app.include_router(apps.router)
    app.include_router(locks.router)
    app.include_router(plugins.router)
    app.include_router(status.router)
    app.include_router(settings_router)
    app.include_router(system.router)

    # Mount the SPA at "/" so a single container serves both API + UI. We
    # only register the mount when the directory exists, otherwise the
    # catch-all would intercept every request and 404 unrelated paths. In
    # tests (CWD = tmp_path) the path doesn't exist; in `just serve` and the
    # Docker image, it does (after `bun run build`). Override the location
    # via ``CURFEW_WEB_DIST`` if the SPA lives elsewhere. ``html=True``
    # falls back to ``index.html`` for client-side routes on hard reload —
    # which is what TanStack Router needs.
    web_dist = Path(os.environ.get("CURFEW_WEB_DIST", "web/dist"))
    if web_dist.is_dir():
        app.mount("/", SPAStaticFiles(directory=web_dist, html=True), name="spa")

    return app
