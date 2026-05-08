"""Runtime-mutable kernel settings.

The ``settings`` table is a single row (``id=1``, enforced by a CheckConstraint
in the model). Defaults are seeded by the initial Alembic migration, so a GET
on a fresh install returns the seeded values rather than 404'ing.

PATCH applies only the fields the client actually sent (``model_dump(exclude_
unset=True)``), so a one-field PATCH doesn't blank the rest. ``Field(gt=0)`` on
each tunable in :class:`SettingsUpdate` rejects zero/negative values at the
boundary with a 422; an ``agent_tick_seconds=0`` would silently busy-loop the
agent if it slipped through.
"""

from __future__ import annotations

from typing import Annotated

from curfew.audit import record_audit
from curfew.db import get_session
from curfew.models import AuditTargetKind, Settings
from curfew.schemas import SettingsRead, SettingsUpdate
from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from curfew_api.auth import RequireAdmin

router = APIRouter(prefix="/v1/settings", tags=["settings"])


def _get_singleton(session: Session) -> Settings:
    """Return the singleton settings row, seeded by the initial migration."""
    row = session.exec(select(Settings)).one()
    return row


@router.get("", response_model=SettingsRead)
def get_settings(
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> Settings:
    return _get_singleton(session)


@router.patch("", response_model=SettingsRead)
def update_settings(
    payload: SettingsUpdate,
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> Settings:
    row = _get_singleton(session)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return row

    for field, value in update_data.items():
        setattr(row, field, value)
    session.add(row)
    session.flush()

    record_audit(
        session,
        actor=actor,
        action="settings.update",
        target_kind=AuditTargetKind.SETTINGS,
        target_id="singleton",
        payload=update_data,
    )
    session.commit()
    session.refresh(row)
    return row
