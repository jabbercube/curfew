"""Manual lock / unlock for a user.

Both endpoints upsert ``user_locks`` and return the updated user-scope
``LockStatus`` (so the operator sees the immediate effect of the toggle, not
an empty ``204``). Both are audited as ``user.lock`` and ``user.unlock``.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Annotated

from curfew.audit import record_audit
from curfew.auth import Actor
from curfew.db import get_session
from curfew.models import AuditTargetKind, User, UserLock
from curfew.rules import user_scope
from curfew.schemas import LockStatus
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from curfew_api.auth import RequireManager

router = APIRouter(prefix="/v1/users", tags=["locks"])


def _set_manual_lock(session: Session, *, username: str, locked: bool, actor: Actor) -> LockStatus:
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")

    lock = session.exec(select(UserLock).where(UserLock.user_id == user.id)).first()
    if lock is None:
        lock = UserLock(
            user_id=user.id,
            manual_lock=locked,
            set_at=datetime.now(UTC),
            set_by=actor.audit_str,
        )
        session.add(lock)
    else:
        lock.manual_lock = locked
        lock.set_at = datetime.now(UTC)
        lock.set_by = actor.audit_str
        session.add(lock)
    session.flush()

    action = "user.lock" if locked else "user.unlock"
    record_audit(
        session,
        actor=actor,
        action=action,
        target_kind=AuditTargetKind.USER,
        target_id=user.username,
    )
    session.commit()

    return user_scope.evaluate(session, user.username)


@router.post("/{user}/lock", response_model=LockStatus)
def lock_user(
    user: str,
    actor: RequireManager,
    session: Annotated[Session, Depends(get_session)],
) -> LockStatus:
    return _set_manual_lock(session, username=user, locked=True, actor=actor)


@router.post("/{user}/unlock", response_model=LockStatus)
def unlock_user(
    user: str,
    actor: RequireManager,
    session: Annotated[Session, Depends(get_session)],
) -> LockStatus:
    return _set_manual_lock(session, username=user, locked=False, actor=actor)
