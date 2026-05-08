"""Lock-status endpoints.

``GET /v1/users/{user}/status`` runs every user-scope rule. The kernel ships
with one (``manual_lock``); features add more by registering into
``curfew.rules.user_scope``.

``GET /v1/devices/{device}/status`` runs every device-scope rule. Empty in
the kernel (the shared-device-lock feature is the first device-scope rule);
returns ``{locked: false, reasons: []}`` when the device exists.

Per PLAN.md §"Users", a user with ``managed=False`` always returns
``{locked: false, reasons: []}`` and the rule pipeline is skipped.
"""

from __future__ import annotations

from typing import Annotated

from curfew.db import get_session
from curfew.models import Device, User
from curfew.rules import device_scope, user_scope
from curfew.schemas import LockStatus
from fastapi import APIRouter, Depends, HTTPException, status
from sqlmodel import Session, select

from curfew_api.auth import Operator

router = APIRouter(tags=["status"])


@router.get("/v1/users/{user}/status", response_model=LockStatus)
def user_status(
    user: str,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> LockStatus:
    row = session.exec(select(User).where(User.username == user)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="user not found")
    if not row.managed:
        return LockStatus(locked=False, reasons=[])
    return user_scope.evaluate(session, row.username)


@router.get("/v1/devices/{device}/status", response_model=LockStatus)
def device_status(
    device: str,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> LockStatus:
    row = session.exec(select(Device).where(Device.slug == device)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return device_scope.evaluate(session, row.slug)
