"""Shared Pydantic schemas for the API and CLI.

Lives in ``core`` (per ADR-014) so the CLI and future SDK consumers can import
the same types the API serves. The CLI is a thin HTTP client and benefits from
the same request/response shapes; SDK consumers use ``Reason`` and
``LockStatus`` as well.

Reasons are deliberately schemaless beyond ``kind`` — adding a new reason or
adding fields to an existing reason is a non-breaking change for clients that
ignore unknown fields. (PLAN.md §"Lock status rule pipeline".)
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from curfew.models import UserRole


class Reason(BaseModel):
    """A single rule's contribution to the lock status.

    Has a required ``kind`` discriminator (e.g. ``"manual_lock"``,
    ``"out_of_schedule"``, ``"budget_exhausted"``); rule-specific detail fields
    are allowed and pass through (``extra="allow"``). Clients render ``kind``
    and ignore unknown extras.
    """

    model_config = ConfigDict(extra="allow")

    kind: str


class LockStatus(BaseModel):
    """``{locked, reasons}`` — the user/device-scope rule pipeline output."""

    locked: bool
    reasons: list[Reason] = Field(default_factory=list)


class UserCreate(BaseModel):
    username: str
    role: UserRole = UserRole.MEMBER
    managed: bool = True


class UserRead(BaseModel):
    id: int
    username: str
    role: UserRole
    managed: bool
