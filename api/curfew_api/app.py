"""FastAPI app factory for curfew-api.

Builds the ``FastAPI`` instance, mounts CORS, attaches the routers. Auth is
enforced via per-route ``Depends(require_operator)``, not as ASGI middleware:
this keeps the OpenAPI schema honest (the security requirement is documented
on each protected route) and lets ``/v1/health`` and ``/v1/openapi.json`` opt
out by simply not including the dependency.
"""

from __future__ import annotations

from curfew.config import get_config
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from curfew_api.routes import health


def create_app() -> FastAPI:
    """Build and return a configured FastAPI app."""
    config = get_config()

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

    return app
