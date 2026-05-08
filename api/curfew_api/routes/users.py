"""User CRUD."""

from __future__ import annotations

from typing import Annotated

from curfew.audit import record_audit
from curfew.db import get_session
from curfew.models import AuditTargetKind, Device, User, UserLock
from curfew.passwords import hash_password
from curfew.schemas import UserCreate, UserRead, UserUpdate
from fastapi import APIRouter, Depends, HTTPException, status
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
    session: Annotated[Session, Depends(get_session)],
) -> User:
    row = _get_user_or_404(session, user)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return row  # nothing to update; idempotent no-op

    original_username = row.username
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
