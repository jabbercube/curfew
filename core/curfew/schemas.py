"""Shared Pydantic schemas for the API and CLI.

Lives in ``core`` (per ADR-014) so the CLI and future SDK consumers can import
the same types the API serves. The CLI is a thin HTTP client and benefits from
the same request/response shapes; SDK consumers use ``Reason`` and
``LockStatus`` as well.

Reasons are deliberately schemaless beyond ``kind`` — adding a new reason or
adding fields to an existing reason is a non-breaking change for clients that
ignore unknown fields. (PLAN.md §"Lock status rule pipeline".)

PATCH update schemas use all-optional fields and ``model_dump(exclude_unset=
True)`` at the call site — only fields the client actually sent get applied,
so a PATCH with one field doesn't blank the rest.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from curfew.models import DeviceOS, DeviceType, UserRole


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


# --- Users --------------------------------------------------------------------


class UserCreate(BaseModel):
    username: str
    role: UserRole = UserRole.MEMBER
    managed: bool = True


class UserRead(BaseModel):
    id: int
    username: str
    role: UserRole
    managed: bool


class UserUpdate(BaseModel):
    """All fields optional; only fields actually sent are applied."""

    username: str | None = None
    role: UserRole | None = None
    managed: bool | None = None


# --- Devices ------------------------------------------------------------------


class DeviceCreate(BaseModel):
    slug: str
    type: DeviceType
    os: DeviceOS
    owner: str | None = None  # username of the owning user; None = shared
    mac: list[str] = Field(default_factory=list)
    managed: bool = True


class DeviceRead(BaseModel):
    id: int
    slug: str
    type: DeviceType
    os: DeviceOS
    owner: str | None  # username (resolved from owner_id)
    mac: list[str]
    managed: bool


class DeviceUpdate(BaseModel):
    slug: str | None = None
    type: DeviceType | None = None
    os: DeviceOS | None = None
    owner: str | None = None  # set to "" to clear owner — see route handler
    mac: list[str] | None = None
    managed: bool | None = None


# --- Apps ---------------------------------------------------------------------


class AppCreate(BaseModel):
    slug: str
    exe_paths: list[str] = Field(default_factory=list)
    process_names: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class AppRead(BaseModel):
    id: int
    slug: str
    exe_paths: list[str]
    process_names: list[str]
    urls: list[str]


class AppUpdate(BaseModel):
    slug: str | None = None
    exe_paths: list[str] | None = None
    process_names: list[str] | None = None
    urls: list[str] | None = None
