"""Auth dependencies: bearer-or-cookie -> Actor, plus role-gated factories.

Two valid authentication paths, checked in order on every protected request:

1. **Bearer root token.** ``Authorization: Bearer <CURFEW_ROOT_TOKEN>`` →
   synthetic ``Actor(kind="root", role=ADMIN)``. Bootstrap + recovery path;
   audit-string stays ``"operator"`` so audit history doesn't fork.
2. **Session cookie.** ``Cookie: curfew_session=<id>`` → looks up the
   ``sessions`` row, joins ``users``, returns
   ``Actor(kind="user", role=user.role, ...)``. The session is *sliding* —
   each authenticated request bumps ``last_seen_at`` and ``expires_at``.

Either succeeds → request proceeds. Both absent → 401.

``require_role(min_role)`` returns a dep that 403s if the actor's role is
below ``min_role``. ``Operator`` (any authenticated) stays as the catch-all
type alias so existing routes need only swap names where they're tightened.
"""

from __future__ import annotations

import secrets
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Annotated

from curfew.auth import Actor, root_actor
from curfew.config import Settings, get_config
from curfew.db import get_session
from curfew.models import User, UserRole, UserSession
from fastapi import Depends, HTTPException, Request, status
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from sqlmodel import Session as DBSession

SESSION_COOKIE = "curfew_session"
SESSION_LIFETIME = timedelta(days=7)
"""Sliding lifetime: refreshed on every authenticated request."""

_bearer_scheme = HTTPBearer(auto_error=False)


def require_actor(
    request: Request,
    credentials: Annotated[HTTPAuthorizationCredentials | None, Depends(_bearer_scheme)],
    config: Annotated[Settings, Depends(get_config)],
    db: Annotated[DBSession, Depends(get_session)],
) -> Actor:
    """Return the authenticated actor; 401 if neither path validates.

    Order matters: bearer first, cookie fallback. A request with a valid
    bearer never touches the sessions table; a request with only a cookie
    pays one indexed lookup + one update.
    """
    if credentials is not None and credentials.scheme.lower() == "bearer":
        if secrets.compare_digest(credentials.credentials, config.root_token):
            actor = root_actor()
            request.state.actor = actor.audit_str
            return actor
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid bearer token",
            headers={"WWW-Authenticate": "Bearer"},
        )

    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id is not None:
        session_actor = _resolve_session(db, session_id)
        if session_actor is not None:
            request.state.actor = session_actor.audit_str
            return session_actor

    raise HTTPException(
        status_code=status.HTTP_401_UNAUTHORIZED,
        detail="Not authenticated",
        headers={"WWW-Authenticate": "Bearer"},
    )


def _resolve_session(db: DBSession, session_id: str) -> Actor | None:
    """Look up + refresh a session; return its Actor, or None if invalid/expired."""
    row = db.get(UserSession, session_id)
    if row is None:
        return None

    now = datetime.now(UTC)
    if row.expires_at <= now:
        # Expired — clean up and reject. (Lazy cleanup; a periodic sweep can
        # do bulk deletes when the table grows.)
        db.delete(row)
        db.commit()
        return None

    user = db.get(User, row.user_id)
    if user is None:
        # Orphaned session row (user was deleted). Drop it.
        db.delete(row)
        db.commit()
        return None

    # Slide the expiry on every successful auth.
    row.last_seen_at = now
    row.expires_at = now + SESSION_LIFETIME
    db.add(row)
    db.commit()

    assert user.id is not None
    return Actor(kind="user", role=user.role, user_id=user.id, username=user.username)


def require_role(min_role: UserRole) -> Callable[[Actor], Actor]:
    """Dep factory: returns a dep that 403s if actor's role is below ``min_role``."""

    def dep(actor: Annotated[Actor, Depends(require_actor)]) -> Actor:
        if not actor.has_role(min_role):
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail=f"requires {min_role.value} role",
            )
        return actor

    return dep


# Type aliases for route annotations. ``Operator`` keeps its name so existing
# routes only swap when tightening; new gates use ``RequireManager`` /
# ``RequireAdmin``.
Operator = Annotated[Actor, Depends(require_actor)]
RequireManager = Annotated[Actor, Depends(require_role(UserRole.MANAGER))]
RequireAdmin = Annotated[Actor, Depends(require_role(UserRole.ADMIN))]
