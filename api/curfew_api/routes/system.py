"""System-level operator tooling.

The snapshot is a one-shot diagnostic dump of live system state — useful for
debugging, support-bundle generation, and ad-hoc backup. It is *not* the
primary read path; clients listing entities should use the per-resource
endpoints.

``audit_log`` and ``agent_tokens`` deliberately do not appear in the row dump:
audit_log is unbounded and grows linearly with operator activity, and
agent_tokens stores secret hashes that have no business in a diagnostic
payload. Their row counts surface in the ``counts`` block instead, so an
operator can see the tables exist and roughly how full they are without
exfiltrating their contents.

Naming note: PLAN.md initially proposed ``/v1/admin/snapshot``; we use
``/v1/system/`` instead to avoid collision with ``User.role=admin`` once
per-user role-based auth lands.
"""

from __future__ import annotations

from typing import Annotated

from curfew.db import get_session
from curfew.models import (
    Agent,
    AgentToken,
    App,
    AuditLog,
    Device,
    Manifest,
    Plugin,
    Settings,
    User,
    UserLock,
)
from curfew.schemas import (
    AgentSnapshotRow,
    AppRead,
    DeviceRead,
    ManifestSnapshotRow,
    PluginSnapshotRow,
    SettingsRead,
    SystemSnapshot,
    UserLockSnapshotRow,
    UserRead,
)
from fastapi import APIRouter, Depends
from sqlalchemy import func
from sqlmodel import Session, select

from curfew_api.auth import RequireAdmin

router = APIRouter(prefix="/v1/system", tags=["system"])


@router.get("/snapshot", response_model=SystemSnapshot)
def get_snapshot(
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> SystemSnapshot:
    settings_row = session.exec(select(Settings)).one()
    audit_count = session.exec(select(func.count()).select_from(AuditLog)).one()
    token_count = session.exec(select(func.count()).select_from(AgentToken)).one()

    return SystemSnapshot(
        users=[
            UserRead.model_validate(r) for r in session.exec(select(User).order_by(User.username))
        ],
        devices=[
            DeviceRead.model_validate(r) for r in session.exec(select(Device).order_by(Device.slug))
        ],
        apps=[AppRead.model_validate(r) for r in session.exec(select(App).order_by(App.slug))],
        agents=[
            AgentSnapshotRow.model_validate(r)
            for r in session.exec(select(Agent).order_by(Agent.device_id))  # type: ignore[arg-type]
        ],
        plugins=[
            PluginSnapshotRow.model_validate(r)
            for r in session.exec(select(Plugin).order_by(Plugin.type, Plugin.instance_id))
        ],
        user_locks=[
            UserLockSnapshotRow.model_validate(r)
            for r in session.exec(select(UserLock).order_by(UserLock.user_id))  # type: ignore[arg-type]
        ],
        manifests=[
            ManifestSnapshotRow.model_validate(r)
            for r in session.exec(select(Manifest).order_by(Manifest.type))
        ],
        settings=SettingsRead.model_validate(settings_row),
        counts={"audit_log": audit_count, "agent_tokens": token_count},
    )
