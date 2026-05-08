"""User CRUD — minimum subset needed for the lock flow.

Ships ``POST /v1/users`` (create) and ``GET /v1/users/{user}`` (read). Full
CRUD (list, patch, delete) lands in a follow-up PR.
"""

from __future__ import annotations

from typing import Annotated

from curfew.audit import record_audit
from curfew.db import get_session
from curfew.models import AuditTargetKind, User
from curfew.schemas import UserCreate, UserRead
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from curfew_api.auth import Operator

router = APIRouter(prefix="/v1/users", tags=["users"])


@router.post("", response_model=UserRead, status_code=status.HTTP_201_CREATED)
def create_user(
    payload: UserCreate,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> User:
    user = User(username=payload.username, role=payload.role, managed=payload.managed)
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
    row = session.exec(select(User).where(User.username == user)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    return row
