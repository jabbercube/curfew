"""Auth dependency for curfew-api.

V1 uses a single root bearer token loaded from ``CURFEW_ROOT_TOKEN`` env (per
PLAN.md §"Authentication"). The dependency yields a string ``actor`` —
``"operator"`` for V1 — that handler code records in audit log writes. Future
per-user role auth replaces this dependency without changing the call sites.
"""

from __future__ import annotations

import secrets
from typing import Annotated

from curfew.config import Settings, get_config
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer

_bearer_scheme = HTTPBearer(auto_error=False)


def require_operator(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    config: Annotated[Settings, Depends(get_config)],
) -> str:
    """Validate the bearer against ``CURFEW_ROOT_TOKEN``; return the actor.

    Returns ``"operator"`` for V1. Per-user auth (a future feature) returns the
    authenticated username instead, slotting in here without changing call sites.
    """
    if credentials is None or credentials.scheme.lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    if not secrets.compare_digest(credentials.credentials, config.root_token):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )
    actor = "operator"
    request.state.actor = actor
    return actor


Operator = Annotated[str, Depends(require_operator)]
