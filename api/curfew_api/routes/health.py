"""``/v1/health`` — liveness probe.

Unauthenticated. Returns ``{status, db}``. The ``db`` field round-trips a
``SELECT 1`` so the operator (and future container orchestrator) can tell
whether the DB connection is alive, not just whether the process is up.
"""

from __future__ import annotations

from typing import Annotated, Literal

from curfew.db import get_session
from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy import text
from sqlmodel import Session

router = APIRouter(tags=["health"])


class HealthResponse(BaseModel):
    status: Literal["ok", "degraded"]
    db: str


@router.get("/v1/health", response_model=HealthResponse)
def health(session: Annotated[Session, Depends(get_session)]) -> HealthResponse:
    try:
        session.execute(text("SELECT 1"))
        return HealthResponse(status="ok", db="ok")
    except Exception as exc:
        return HealthResponse(status="degraded", db=type(exc).__name__)
