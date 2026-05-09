"""Plugin discovery + assignment CRUD.

The router serves both halves of the plugin surface:

- ``GET /v1/plugins/types`` (step 10) — what curfew-core discovered at
  startup. Operators see this to know which plugin types they can assign.
- ``GET/POST/PATCH/DELETE /v1/plugins`` (step 11) — operator's assignments.
  POST/PATCH/DELETE are admin-gated and audited; reads are
  any-authenticated. Each write goes through the in-memory ``PluginRuntime``
  so the live instance map stays in lock-step with the
  ``plugin_assignments`` table.

Per ADR-005 + PLUGINS.md §"Lifecycle": assigning a plugin instantiates it;
unassigning drops it; PATCHing config re-instantiates it. Operators don't
have to restart curfew-core for any of this. Only adding *new* plugin
folders requires a restart (discovery runs once at startup, ADR-013).
"""

from __future__ import annotations

from typing import Annotated

from curfew.audit import record_audit
from curfew.db import get_session
from curfew.models import AuditTargetKind, PluginAssignment
from curfew.plugin_loader import PluginRegistry, PluginType
from curfew.plugin_runtime import (
    AlreadyAssignedError,
    NotAssignedError,
    PluginInstantiationError,
    PluginRuntime,
    UnknownPluginTypeError,
)
from curfew.schemas import (
    PluginAssignmentCreate,
    PluginAssignmentRead,
    PluginAssignmentUpdate,
    PluginTypeRead,
)
from fastapi import APIRouter, Depends, HTTPException, Request, status
from pydantic import ValidationError
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from curfew_api.auth import Operator, RequireAdmin

router = APIRouter(prefix="/v1/plugins", tags=["plugins"])


# --- /v1/plugins/types -------------------------------------------------------


def _type_to_read(ptype: PluginType) -> PluginTypeRead:
    m = ptype.manifest
    return PluginTypeRead(
        type=ptype.type,
        name=m.name if m else None,
        version=m.version if m else None,
        description=m.description if m else None,
        config_schema=m.config_schema if m else None,
        error=ptype.error,
        has_requirements_txt=ptype.has_requirements_txt,
    )


@router.get("/types", response_model=list[PluginTypeRead])
def list_plugin_types(request: Request, actor: Operator) -> list[PluginTypeRead]:
    """List discovered plugin types from ``CURFEW_PLUGINS_DIRS``.

    Reads from the registry built once at startup. Adding or removing a
    plugin folder requires a curfew-core restart (per ADR-013); this
    endpoint just reports what was discovered.
    """
    registry: PluginRegistry = request.app.state.plugin_registry
    return [_type_to_read(p) for p in registry.all()]


# --- /v1/plugins (assignment CRUD) ------------------------------------------


def _row_to_read(row: PluginAssignment) -> PluginAssignmentRead:
    return PluginAssignmentRead(
        type=row.type,
        instance_id=row.instance_id,
        config=dict(row.config),
        users=list(row.users),
        enabled=row.enabled,
    )


def _runtime(request: Request) -> PluginRuntime:
    return request.app.state.plugin_runtime  # type: ignore[no-any-return]


def _get_assignment_or_404(session: Session, type_name: str, instance_id: str) -> PluginAssignment:
    row = session.exec(
        select(PluginAssignment).where(
            PluginAssignment.type == type_name,
            PluginAssignment.instance_id == instance_id,
        )
    ).first()
    if row is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plugin {type_name}/{instance_id} not assigned",
        )
    return row


@router.get("", response_model=list[PluginAssignmentRead])
def list_assignments(
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> list[PluginAssignmentRead]:
    rows = session.exec(
        select(PluginAssignment).order_by(PluginAssignment.type, PluginAssignment.instance_id)
    ).all()
    return [_row_to_read(r) for r in rows]


@router.post("", response_model=PluginAssignmentRead, status_code=status.HTTP_201_CREATED)
async def create_assignment(
    payload: PluginAssignmentCreate,
    actor: RequireAdmin,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> PluginAssignmentRead:
    """Assign a plugin to a set of users.

    Validates the type exists (404), the config matches the type's Pydantic
    schema (422), and the ``(type, instance_id)`` is unique (409). Builds
    the live instance *before* writing the DB row so a bad config / broken
    plugin returns 422 / 500 without leaving an orphan row behind.
    """
    runtime = _runtime(request)
    try:
        instance = runtime.build_instance(payload.type, dict(payload.config))
    except UnknownPluginTypeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    except PluginInstantiationError as exc:
        # The plugin's __init__ raised. Not the operator's fault per se,
        # but actionable. 500 with detail surfaces it.
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    row = PluginAssignment(
        type=payload.type,
        instance_id=payload.instance_id,
        config=dict(payload.config),
        users=list(payload.users),
        enabled=True,
    )
    session.add(row)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"plugin {payload.type}/{payload.instance_id} already assigned",
        ) from None

    record_audit(
        session,
        actor=actor,
        action="plugin.assign",
        target_kind=AuditTargetKind.PLUGIN,
        target_id=f"{row.type}/{row.instance_id}",
        payload={"users": list(row.users)},
    )
    session.commit()

    try:
        await runtime.register(
            type_name=row.type,
            instance_id=row.instance_id,
            instance=instance,
            config=dict(row.config),
            users=list(row.users),
            enabled=row.enabled,
        )
    except AlreadyAssignedError:
        # Race: a concurrent request beat us to the runtime register call.
        # The DB row we just inserted survived, so the existing instance
        # is governing this slot — return its read shape.
        pass

    return _row_to_read(row)


@router.patch("/{type_name}/{instance_id}", response_model=PluginAssignmentRead)
async def update_assignment(
    type_name: str,
    instance_id: str,
    payload: PluginAssignmentUpdate,
    actor: RequireAdmin,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> PluginAssignmentRead:
    """Partial update.

    If ``config`` changes, the runtime re-instantiates the plugin
    (PLAN.md option 1). ``users`` and ``enabled`` updates don't touch
    ``__init__``.
    """
    runtime = _runtime(request)
    row = _get_assignment_or_404(session, type_name, instance_id)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return _row_to_read(row)

    # Apply runtime mutation first so a bad config (failed re-instantiation)
    # surfaces before we touch the DB. The runtime hasn't committed yet —
    # if the runtime call raises, no DB write happens.
    try:
        await runtime.update(
            type_name=type_name,
            instance_id=instance_id,
            config=update_data.get("config"),
            users=update_data.get("users"),
            enabled=update_data.get("enabled"),
        )
    except NotAssignedError:
        # Runtime out of sync with DB — should never happen for a row that
        # passed _get_assignment_or_404. Treat as 404 rather than corrupt
        # the DB.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"plugin {type_name}/{instance_id} not loaded in runtime",
        ) from None
    except UnknownPluginTypeError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=exc.errors(),
        ) from exc
    except PluginInstantiationError as exc:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail=str(exc)
        ) from exc

    if "config" in update_data:
        row.config = dict(update_data["config"])
    if "users" in update_data:
        row.users = list(update_data["users"])
    if "enabled" in update_data:
        row.enabled = bool(update_data["enabled"])
    session.add(row)
    record_audit(
        session,
        actor=actor,
        action="plugin.update",
        target_kind=AuditTargetKind.PLUGIN,
        target_id=f"{row.type}/{row.instance_id}",
        payload=update_data,
    )
    session.commit()
    session.refresh(row)
    return _row_to_read(row)


@router.delete("/{type_name}/{instance_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_assignment(
    type_name: str,
    instance_id: str,
    actor: RequireAdmin,
    request: Request,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    runtime = _runtime(request)
    row = _get_assignment_or_404(session, type_name, instance_id)
    session.delete(row)
    record_audit(
        session,
        actor=actor,
        action="plugin.unassign",
        target_kind=AuditTargetKind.PLUGIN,
        target_id=f"{row.type}/{row.instance_id}",
    )
    session.commit()
    await runtime.unassign(type_name=type_name, instance_id=instance_id)
