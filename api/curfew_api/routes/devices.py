"""Device CRUD.

API uses ``owner: str | None`` (the owning user's username), translated to/from
``owner_id`` internally. Owner lookup is one query per device — fine at homelab
scale (≤30 devices). If that ever shows up in profiling, swap to a single LEFT
JOIN in ``list_devices``.
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

from curfew_api.auth import Operator

router = APIRouter(prefix="/v1/devices", tags=["devices"])


def _resolve_owner_username(session: Session, owner_id: int | None) -> str | None:
    if owner_id is None:
        return None
    user = session.get(User, owner_id)
    return user.username if user is not None else None


def _resolve_owner_id(session: Session, owner_username: str | None) -> int | None:
    """Look up owner_id by username; raise 404 if a non-null username doesn't exist."""
    if owner_username is None:
        return None
    user = session.exec(select(User).where(User.username == owner_username)).first()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"owner user {owner_username!r} not found",
        )
    assert user.id is not None
    return user.id


def _to_read(session: Session, device: Device) -> DeviceRead:
    return DeviceRead(
        id=device.id,  # type: ignore[arg-type]  # populated by DB after flush
        slug=device.slug,
        type=device.type,
        os=device.os,
        owner=_resolve_owner_username(session, device.owner_id),
        mac=list(device.mac),
        managed=device.managed,
    )


def _get_device_or_404(session: Session, slug: str) -> Device:
    row = session.exec(select(Device).where(Device.slug == slug)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="device not found")
    return row


@router.get("", response_model=list[DeviceRead])
def list_devices(
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> list[DeviceRead]:
    rows = session.exec(select(Device).order_by(Device.slug)).all()
    return [_to_read(session, d) for d in rows]


@router.post("", response_model=DeviceRead, status_code=status.HTTP_201_CREATED)
def create_device(
    payload: DeviceCreate,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> DeviceRead:
    owner_id = _resolve_owner_id(session, payload.owner)
    device = Device(
        slug=payload.slug,
        owner_id=owner_id,
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
            "owner": payload.owner,
            "managed": device.managed,
        },
    )
    session.commit()
    session.refresh(device)
    return _to_read(session, device)


@router.get("/{device}", response_model=DeviceRead)
def get_device(
    device: str,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> DeviceRead:
    return _to_read(session, _get_device_or_404(session, device))


@router.patch("/{device}", response_model=DeviceRead)
def update_device(
    device: str,
    payload: DeviceUpdate,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> DeviceRead:
    row = _get_device_or_404(session, device)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return _to_read(session, row)

    original_slug = row.slug
    audit_payload = dict(update_data)  # what the client sent, for the audit row

    # Owner is the only field that needs username→id translation; pop it out so
    # the generic setattr loop below doesn't try to set Device.owner (which
    # doesn't exist — the model has owner_id).
    if "owner" in update_data:
        row.owner_id = _resolve_owner_id(session, update_data.pop("owner"))

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
        payload=audit_payload,
    )
    session.commit()
    session.refresh(row)
    return _to_read(session, row)


@router.delete("/{device}", status_code=status.HTTP_204_NO_CONTENT)
def delete_device(
    device: str,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    row = _get_device_or_404(session, device)

    # 409 if an agent is installed on the device; operator must `agent uninstall`
    # first. Cascading the agent + its tokens would be silent privilege loss.
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
