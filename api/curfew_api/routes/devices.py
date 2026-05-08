"""Device CRUD.

API uses ``owner_id: int | None`` directly — same shape the DB stores. Clients
that hold usernames look up the id once via ``GET /v1/users/{user}`` and post
the id. No translation layer in this module.
"""

from __future__ import annotations

from typing import Annotated

from curfew.audit import record_audit
from curfew.db import get_session
from curfew.models import Agent, AuditTargetKind, Device, User
from curfew.schemas import DeviceCreate, DeviceRead, DeviceUpdate
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from curfew_api.auth import Operator, RequireAdmin

router = APIRouter(prefix="/v1/devices", tags=["devices"])


def _get_device_or_404(session: Session, slug: str) -> Device:
    row = session.exec(select(Device).where(Device.slug == slug)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return row


def _validate_owner_id(session: Session, owner_id: int | None) -> None:
    """Pre-check that the owner_id resolves to an existing user.

    Catching the FK violation post-INSERT would also work but yields opaque
    errors. A pre-check gives a clean 404 with the offending id.
    """
    if owner_id is None:
        return
    if session.get(User, owner_id) is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"owner_id {owner_id} not found",
        )


@router.get("", response_model=list[DeviceRead])
def list_devices(
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> list[Device]:
    return list(session.exec(select(Device).order_by(Device.slug)).all())


@router.post("", response_model=DeviceRead, status_code=status.HTTP_201_CREATED)
def create_device(
    payload: DeviceCreate,
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> Device:
    _validate_owner_id(session, payload.owner_id)
    device = Device(
        slug=payload.slug,
        owner_id=payload.owner_id,
        type=payload.type,
        os=payload.os,
        mac=payload.mac,
        managed=payload.managed,
    )
    session.add(device)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"device slug {payload.slug!r} already exists",
        ) from None
    record_audit(
        session,
        actor=actor,
        action="device.create",
        target_kind=AuditTargetKind.DEVICE,
        target_id=device.slug,
        payload={
            "type": device.type.value,
            "os": device.os.value,
            "owner_id": device.owner_id,
            "managed": device.managed,
        },
    )
    session.commit()
    session.refresh(device)
    return device


@router.get("/{device}", response_model=DeviceRead)
def get_device(
    device: str,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> Device:
    return _get_device_or_404(session, device)


@router.patch("/{device}", response_model=DeviceRead)
def update_device(
    device: str,
    payload: DeviceUpdate,
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> Device:
    row = _get_device_or_404(session, device)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return row

    if "owner_id" in update_data:
        _validate_owner_id(session, update_data["owner_id"])

    original_slug = row.slug
    for field, value in update_data.items():
        setattr(row, field, value)
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"device slug {update_data.get('slug')!r} already exists",
        ) from None

    record_audit(
        session,
        actor=actor,
        action="device.update",
        target_kind=AuditTargetKind.DEVICE,
        target_id=original_slug,
        payload=update_data,
    )
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{device}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(
    device: str,
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    row = _get_device_or_404(session, device)

    # 409 if an agent is installed; operator must `agent uninstall` first.
    has_agent = session.exec(select(Agent).where(Agent.device_id == row.id)).first()
    if has_agent is not None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"device {device!r} has an agent installed; uninstall it first",
        )

    session.delete(row)
    record_audit(
        session,
        actor=actor,
        action="device.delete",
        target_kind=AuditTargetKind.DEVICE,
        target_id=device,
    )
    session.commit()
