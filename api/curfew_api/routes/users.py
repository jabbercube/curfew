"""User CRUD."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from curfew.audit import record_audit
from curfew.db import get_session
from curfew.models import AuditTargetKind, Device, User, UserLock
from curfew.passwords import hash_password
from curfew.schemas import UserCreate, UserRead, UserUpdate
from fastapi import APIRouter, BackgroundTasks, Depends, HTTPException, Request, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from curfew_api.auth import Operator, RequireAdmin

router = APIRouter(prefix="/v1/users", tags=["users"])


def _get_user_or_404(session: Session, username: str) -> User:
    row = session.exec(select(User).where(User.username == username)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return row


@router.get("", response_model=list[UserRead])
def list_users(
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> list[User]:
    return list(session.exec(select(User).order_by(User.username)).all())


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> User:
    user = User(
        username=payload.username,
        role=payload.role,
        managed=payload.managed,
        password_hash=hash_password(payload.password),
    )
    session.add(user)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"username {payload.username!r} already exists",
        ) from None
    record_audit(
        session,
        actor=actor,
        action="user.create",
        target_kind=AuditTargetKind.USER,
        target_id=user.username,
        payload={"role": user.role.value, "managed": user.managed},
    )
    session.commit()
    session.refresh(user)
    return user


@router.get("/{user}", response_model=UserRead)
def get_user(
    user: str,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> User:
    return _get_user_or_404(session, user)


@router.patch("/{user}", response_model=UserRead)
def update_user(
    user: str,
    payload: UserUpdate,
    actor: RequireAdmin,
    background_tasks: BackgroundTasks,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> User:
    row = _get_user_or_404(session, user)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return row  # nothing to update; idempotent no-op

    original_username = row.username

    # Invariant: unmanaged users are never locked at the DB level. If the
    # PATCH flips ``managed`` to false on a currently-locked user, clear
    # the lock first so the audit trail reads ``user.unlock`` then
    # ``user.update`` (semantic "we unlocked them as part of unmanaging
    # them"), and schedule a plugin reconcile so any external state
    # (smart plug, DNS sinkhole, etc.) clears too. The opposite direction
    # (``managed=true`` on an unmanaged user) is left untouched on
    # purpose — the operator chose to manage them; we don't auto-lock.
    schedule_unlock_reconcile = False
    if update_data.get("managed") is False:
        lock = session.exec(select(UserLock).where(UserLock.user_id == row.id)).first()
        if lock is not None and lock.manual_lock:
            lock.manual_lock = False
            lock.set_at = datetime.now(UTC)
            lock.set_by = actor.audit_str
            session.add(lock)
            session.flush()
            record_audit(
                session,
                actor=actor,
                action="user.unlock",
                target_kind=AuditTargetKind.USER,
                target_id=original_username,
                payload={"reason": "managed_to_unmanaged"},
            )
            schedule_unlock_reconcile = True

    for field, value in update_data.items():
        setattr(row, field, value)
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"username {update_data.get('username')!r} already exists",
        ) from None

    record_audit(
        session,
        actor=actor,
        action="user.update",
        target_kind=AuditTargetKind.USER,
        target_id=original_username,
        payload=update_data,
    )
    session.commit()
    session.refresh(row)

    if schedule_unlock_reconcile:
        # Fire after the response so the operator's PATCH doesn't wait on
        # plugins. The runtime reads the post-commit state — user is now
        # ``managed=false, manual_lock=false``; the rule pipeline returns
        # ``locked=false`` and plugins reconcile to the unlocked state
        # before treating this user as out-of-scope.
        background_tasks.add_task(request.app.state.plugin_runtime.dispatch_for_user, row.username)

    return row


@router.delete("/{user}", status_code=status.HTTP_204_NO_CONTENT)
def delete_user(
    user: str,
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    row = _get_user_or_404(session, user)

    # 409 if any device still references this user — operator must reassign
    # or delete those devices first. We don't cascade silently because losing
    # device records to a typo'd `delete user` would be hard to recover from.
    has_devices = session.exec(select(Device).where(Device.owner_id == row.id)).first()
    if has_devices is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"user {user!r} owns one or more devices; reassign or delete first",
        )

    # The user_lock row is metadata of the user (1:1, not its own entity to
    # protect). Cascade-delete it so the user can be removed cleanly. Flush
    # explicitly between the two deletes so SQLite's FK enforcement sees the
    # child gone before the parent — SQLAlchemy can't infer the order without
    # a declared relationship().
    lock = session.exec(select(UserLock).where(UserLock.user_id == row.id)).first()
    if lock is not None:
        session.delete(lock)
        session.flush()

    session.delete(row)
    record_audit(
        session,
        actor=actor,
        action="user.delete",
        target_kind=AuditTargetKind.USER,
        target_id=user,
    )
    session.commit()
