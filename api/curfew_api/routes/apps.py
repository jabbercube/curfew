"""App catalog CRUD.

The ``apps`` table is the global catalog of blockable executables/processes/
URLs. Per-user app lists (``users.target_apps``) are deferred until the first
concrete agent (``windows-agent``) needs to consume them — see the deferred
decision in PLAN.md §"Per-rule user config — decision deferred". Until that
lands, apps exist in the catalog but nothing references them.
"""

from __future__ import annotations

from typing import Annotated

from curfew.audit import record_audit
from curfew.db import get_session
from curfew.models import App, AuditTargetKind
from curfew.schemas import AppCreate, AppRead, AppUpdate
from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, select

from curfew_api.auth import Operator, RequireAdmin

router = APIRouter(prefix="/v1/apps", tags=["apps"])


def _get_app_or_404(session: Session, slug: str) -> App:
    row = session.exec(select(App).where(App.slug == slug)).first()
    if row is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="app not found")
    return row


@router.get("", response_model=list[AppRead])
def list_apps(
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> list[App]:
    return list(session.exec(select(App).order_by(App.slug)).all())


@router.post("", response_model=AppRead, status_code=status.HTTP_201_CREATED)
def create_app(
    payload: AppCreate,
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> App:
    app = App(
        slug=payload.slug,
        exe_paths=payload.exe_paths,
        process_names=payload.process_names,
        urls=payload.urls,
    )
    session.add(app)
    try:
        session.flush()
    except IntegrityError:
        session.rollback()
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"app slug {payload.slug!r} already exists",
        ) from None
    record_audit(
        session,
        actor=actor,
        action="app.create",
        target_kind=AuditTargetKind.APP,
        target_id=app.slug,
        payload={
            "exe_paths": list(app.exe_paths),
            "process_names": list(app.process_names),
            "urls": list(app.urls),
        },
    )
    session.commit()
    session.refresh(app)
    return app


@router.get("/{app}", response_model=AppRead)
def get_app(
    app: str,
    actor: Operator,
    session: Annotated[Session, Depends(get_session)],
) -> App:
    return _get_app_or_404(session, app)


@router.patch("/{app}", response_model=AppRead)
def update_app(
    app: str,
    payload: AppUpdate,
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> App:
    row = _get_app_or_404(session, app)
    update_data = payload.model_dump(exclude_unset=True)
    if not update_data:
        return row

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
            detail=f"app slug {update_data.get('slug')!r} already exists",
        ) from None

    record_audit(
        session,
        actor=actor,
        action="app.update",
        target_kind=AuditTargetKind.APP,
        target_id=original_slug,
        payload=update_data,
    )
    session.commit()
    session.refresh(row)
    return row


@router.delete("/{app}", status_code=status.HTTP_204_NO_CONTENT)
def delete_app(
    app: str,
    actor: RequireAdmin,
    session: Annotated[Session, Depends(get_session)],
) -> None:
    row = _get_app_or_404(session, app)
    session.delete(row)
    record_audit(
        session,
        actor=actor,
        action="app.delete",
        target_kind=AuditTargetKind.APP,
        target_id=app,
    )
    session.commit()
