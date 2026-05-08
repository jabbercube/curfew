"""Login / logout / me / change-password.

Sessions are server-side: ``POST /v1/auth/login`` authenticates by hashing
comparison and inserts a row into ``sessions`` keyed by a fresh 256-bit
random token. That token is set as the ``curfew_session`` cookie (HttpOnly,
SameSite=Lax, Secure when the request was https). Subsequent requests carry
the cookie; the auth dep resolves it back to the user.

Logout deletes the session row + clears the cookie. ``/me`` is an
introspection endpoint the frontend hits on load to populate nav state.
``change-password`` is the user's self-service endpoint; admin-managed
password resets ride on the admin's user-update path (out of scope here).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from typing import Annotated

from curfew.audit import record_audit
from curfew.db import get_session
from curfew.models import AuditTargetKind, User, UserSession
from curfew.passwords import hash_password, verify_password
from curfew.schemas import ChangePasswordRequest, LoginRequest, MeResponse
from fastapi import APIRouter, Depends, HTTPException, Request, Response, status
from sqlmodel import Session as DBSession
from sqlmodel import select

from curfew_api.auth import SESSION_COOKIE, SESSION_LIFETIME, Operator

router = APIRouter(prefix="/v1/auth", tags=["auth"])


def _set_session_cookie(response: Response, request: Request, session_id: str) -> None:
    """Set the session cookie. ``Secure`` flag mirrors the request scheme."""
    response.set_cookie(
        key=SESSION_COOKIE,
        value=session_id,
        max_age=int(SESSION_LIFETIME.total_seconds()),
        httponly=True,
        secure=request.url.scheme == "https",
        samesite="lax",
        path="/",
    )


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: Annotated[DBSession, Depends(get_session)],
) -> None:
    user = db.exec(select(User).where(User.username == payload.username)).first()
    # Same 401 for unknown user, no password set, and wrong password — never
    # leak which case applies.
    if user is None or user.password_hash is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )
    if not verify_password(payload.password, user.password_hash):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid credentials",
        )

    session_id = secrets.token_urlsafe(32)
    now = datetime.now(UTC)
    assert user.id is not None
    db.add(
        UserSession(
            id=session_id,
            user_id=user.id,
            created_at=now,
            expires_at=now + SESSION_LIFETIME,
            last_seen_at=now,
        )
    )
    record_audit(
        db,
        actor=user.username,
        action="auth.login",
        target_kind=AuditTargetKind.USER,
        target_id=user.username,
    )
    db.commit()

    _set_session_cookie(response, request, session_id)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(
    actor: Operator,
    request: Request,
    response: Response,
    db: Annotated[DBSession, Depends(get_session)],
) -> None:
    # Only cookie sessions need cleanup. Bearer-token requests also hit this
    # path but have nothing to delete; we still clear the cookie for them
    # cheaply to keep the call idempotent.
    session_id = request.cookies.get(SESSION_COOKIE)
    if session_id is not None:
        row = db.get(UserSession, session_id)
        if row is not None:
            db.delete(row)
    record_audit(
        db,
        actor=actor,
        action="auth.logout",
        target_kind=AuditTargetKind.USER,
        target_id=actor.audit_str,
    )
    db.commit()
    response.delete_cookie(SESSION_COOKIE, path="/")


@router.get("/me", response_model=MeResponse)
def me(actor: Operator) -> MeResponse:
    return MeResponse(kind=actor.kind, username=actor.username, role=actor.role)


@router.post("/change-password", status_code=status.HTTP_204_NO_CONTENT)
def change_password(
    payload: ChangePasswordRequest,
    actor: Operator,
    db: Annotated[DBSession, Depends(get_session)],
) -> None:
    if actor.kind != "user":
        # Root token has no password to change; force the operator to do this
        # as a real user.
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="root token cannot change a password; log in as a user",
        )
    assert actor.user_id is not None
    user = db.get(User, actor.user_id)
    assert user is not None  # actor came from a valid session, user must exist
    if user.password_hash is None or not verify_password(
        payload.current_password, user.password_hash
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="current password incorrect",
        )

    user.password_hash = hash_password(payload.new_password)
    db.add(user)
    record_audit(
        db,
        actor=actor,
        action="auth.change_password",
        target_kind=AuditTargetKind.USER,
        target_id=user.username,
    )
    db.commit()
