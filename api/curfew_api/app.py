"""FastAPI app factory for curfew-api.

Builds the ``FastAPI`` instance, registers the kernel's lock-status rules,
mounts CORS, and attaches the routers. Auth is enforced via per-route
``Depends(require_operator)``, not as ASGI middleware: this keeps the OpenAPI
schema honest (the security requirement is documented on each protected
route) and lets ``/v1/health`` and ``/v1/openapi.json`` opt out by simply not
including the dependency.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from curfew.config import get_config
from curfew.plugin_loader import discover_plugins
from curfew.rules import register_kernel_rules
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles
from starlette.exceptions import HTTPException as StarletteHTTPException
from starlette.responses import Response

from curfew_api.routes import apps, auth, devices, health, locks, plugins, status, system, users
from curfew_api.routes.settings import router as settings_router


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


def create_app() -> FastAPI:
    """Build and return a configured FastAPI app."""
    config = get_config()
    register_kernel_rules()

    app = FastAPI(
        title="curfew-core",
        description="curfew API service",
        version="0.0.0",
        openapi_url="/v1/openapi.json",
    )

    # Discover plugins once at startup. The registry is read-only at
    # runtime; adding a new plugin folder requires a restart (ADR-013).
    # ``discover_plugins`` is robust to missing dirs and per-plugin
    # errors, so a misconfigured ``CURFEW_PLUGINS_DIRS`` doesn't stop
    # startup — bad plugins surface at ``GET /v1/plugins/types``.
    app.state.plugin_registry = discover_plugins(config.plugins_dirs)

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
