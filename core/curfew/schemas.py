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

from datetime import datetime

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
    password: str = Field(min_length=8)
    role: UserRole = UserRole.MEMBER
    managed: bool = True


class UserRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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
    owner_id: int | None = None  # FK to users.id; None = shared device
    mac: list[str] = Field(default_factory=list)
    managed: bool = True


class DeviceRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: int
    slug: str
    type: DeviceType
    os: DeviceOS
    owner_id: int | None
    mac: list[str]
    managed: bool


class DeviceUpdate(BaseModel):
    """All fields optional. Send ``owner_id: null`` to clear the owner."""

    slug: str | None = None
    type: DeviceType | None = None
    os: DeviceOS | None = None
    owner_id: int | None = None
    mac: list[str] | None = None
    managed: bool | None = None


# --- Apps ---------------------------------------------------------------------


class AppCreate(BaseModel):
    slug: str
    exe_paths: list[str] = Field(default_factory=list)
    process_names: list[str] = Field(default_factory=list)
    urls: list[str] = Field(default_factory=list)


class AppRead(BaseModel):
    model_config = ConfigDict(from_attributes=True)

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


# --- Auth ---------------------------------------------------------------------


class LoginRequest(BaseModel):
    username: str
    password: str


class MeResponse(BaseModel):
    """Identity payload for the current actor — drives frontend nav state."""

    kind: str  # "root" | "user"
    username: str | None
    role: UserRole


class ChangePasswordRequest(BaseModel):
    current_password: str
    new_password: str = Field(min_length=8)


# --- Settings -----------------------------------------------------------------


class SettingsRead(BaseModel):
    """The runtime-mutable kernel tunables (singleton row)."""

    model_config = ConfigDict(from_attributes=True)

    agent_tick_seconds: int
    manifest_tick_seconds: int
    plugin_resync_seconds: int
    plugin_reconcile_timeout_seconds: int
    audit_retention_days: int


class SettingsUpdate(BaseModel):
    """Partial update; ``Field(gt=0)`` rejects zero/negative tick values at the boundary."""

    agent_tick_seconds: int | None = Field(default=None, gt=0)
    manifest_tick_seconds: int | None = Field(default=None, gt=0)
    plugin_resync_seconds: int | None = Field(default=None, gt=0)
    plugin_reconcile_timeout_seconds: int | None = Field(default=None, gt=0)
    audit_retention_days: int | None = Field(default=None, gt=0)


# --- System snapshot ----------------------------------------------------------
#
# These ``*SnapshotRow`` shapes are deliberately minimal — they exist for the
# diagnostic dump and don't aspire to be full Read schemas. When the
# corresponding CRUD lands (agents, plugins, manifests), promote to proper
# Read schemas and let the snapshot reuse them.


class AgentSnapshotRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    device_id: int
    type: str
    last_heartbeat: datetime | None
    last_seen_version: str | None


class PluginAssignmentRead(BaseModel):
    """One assigned plugin instance — what ``GET /v1/plugins`` returns.

    Mirrors the ``plugin_assignments`` row + the in-memory enabled flag.
    Doesn't surface the live ``Plugin`` instance object; that's a runtime
    concern, not part of the API contract.
    """

    model_config = ConfigDict(from_attributes=True)

    type: str
    instance_id: str
    config: dict[str, object]
    users: list[str]
    enabled: bool


class PluginAssignmentCreate(BaseModel):
    """``POST /v1/plugins`` body. ``instance_id`` defaults to ``"default"``."""

    type: str
    instance_id: str = "default"
    config: dict[str, object] = Field(default_factory=dict)
    users: list[str] = Field(default_factory=list)


class PluginAssignmentUpdate(BaseModel):
    """``PATCH /v1/plugins/{type}/{instance_id}`` body. All-optional."""

    config: dict[str, object] | None = None
    users: list[str] | None = None
    enabled: bool | None = None


class PluginTypeRead(BaseModel):
    """One discovered plugin type as returned by ``GET /v1/plugins/types``.

    Reflects what the loader found at startup. ``error`` is non-null when
    the plugin folder failed to load (missing manifest, no Plugin subclass,
    bad imports, etc.); successfully-loaded plugins have ``error: null``
    and the manifest fields populated.
    """

    type: str
    name: str | None = None
    version: str | None = None
    description: str | None = None
    config_schema: str | None = None
    error: str | None = None
    has_requirements_txt: bool = False


class PluginSnapshotRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: str
    instance_id: str
    users: list[str]
    enabled: bool


class UserLockSnapshotRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    user_id: int
    manual_lock: bool
    set_at: datetime
    set_by: str | None


class ManifestSnapshotRow(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    type: str
    version: str
    sha256: str
    url: str


class SystemSnapshot(BaseModel):
    """Aggregate dump of live system state for diagnostics / backup.

    ``audit_log`` and ``agent_tokens`` deliberately stay out of the dump:
    audit_log is unbounded; agent_tokens stores secret hashes. The ``counts``
    block surfaces the row counts so operators still know they exist.
    """

    users: list[UserRead]
    devices: list[DeviceRead]
    apps: list[AppRead]
    agents: list[AgentSnapshotRow]
    plugins: list[PluginSnapshotRow]
    user_locks: list[UserLockSnapshotRow]
    manifests: list[ManifestSnapshotRow]
    settings: SettingsRead
    counts: dict[str, int]
