"""FastAPI app factory for curfew-api.

Builds the ``FastAPI`` instance, registers the kernel's lock-status rules,
mounts CORS, and attaches the routers. Auth is enforced via per-route
``Depends(require_operator)``, not as ASGI middleware: this keeps the OpenAPI
schema honest (the security requirement is documented on each protected
route) and lets ``/v1/health`` and ``/v1/openapi.json`` opt out by simply not
including the dependency.
"""

from __future__ import annotations

from curfew.config import get_config
from curfew.rules import register_kernel_rules
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from curfew_api.routes import health, locks, status, users


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

    if config.cors_origins:
        app.add_middleware(
            CORSMiddleware,
            allow_origins=config.cors_origins,
            allow_credentials=True,
            allow_methods=["*"],
            allow_headers=["*"],
        )

    app.include_router(health.router)
    app.include_router(users.router)
    app.include_router(locks.router)
    app.include_router(status.router)

    return app
