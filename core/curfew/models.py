"""Kernel storage models for curfew.

All 10 tables defined per docs/PLAN.md §"Tables", with the schema deviations
documented in the storage-kernel PR. Adding a new feature usually means adding a
new table or columns; the existing tables are stable contracts.
"""

from __future__ import annotations

from datetime import UTC, datetime
from enum import StrEnum
from typing import Any
from uuid import UUID, uuid4

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    DateTime,
    Index,
    TypeDecorator,
    UniqueConstraint,
)
from sqlalchemy import (
    Enum as SAEnum,
)
from sqlmodel import Field, SQLModel


class UTCDateTime(TypeDecorator[datetime]):
    """Datetime that always round-trips as timezone-aware UTC.

    SQLite stores DateTime as ISO strings without preserving tzinfo, so a naive
    DateTime(timezone=True) silently strips zone info on read. This decorator
    rejects naive datetimes on write and re-attaches UTC on read so callers
    never see a naive datetime.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            raise ValueError(f"naive datetime rejected: {value!r}")
        return value.astimezone(UTC)

    def process_result_value(self, value: datetime | None, dialect: Any) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            return value.replace(tzinfo=UTC)
        return value.astimezone(UTC)


# --- MetaData naming convention ------------------------------------------------
# Stable constraint names so Alembic auto-gen produces deterministic migrations
# and constraint references survive across machines / environments.

SQLModel.metadata.naming_convention = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


# --- Enums ---------------------------------------------------------------------


class UserRole(StrEnum):
    MEMBER = "member"
    MANAGER = "manager"
    ADMIN = "admin"


class DeviceType(StrEnum):
    """Device form-factor.

    LAPTOP folds into PC: enforcement (NTFS ACL, registry policy, process kill)
    is identical for laptops and desktops. If a future feature needs to
    distinguish them, it can add the column then.
    """

    PC = "pc"
    PHONE = "phone"
    TABLET = "tablet"
    CONSOLE = "console"
    TV = "tv"


class DeviceOS(StrEnum):
    WINDOWS = "windows"
    MACOS = "macos"
    LINUX = "linux"
    IOS = "ios"
    ANDROID = "android"


class AuditTargetKind(StrEnum):
    USER = "user"
    DEVICE = "device"
    APP = "app"
    AGENT = "agent"
    PLUGIN = "plugin"
    SETTINGS = "settings"
    MANIFEST = "manifest"


# --- Helpers -------------------------------------------------------------------


def _utcnow() -> datetime:
    return datetime.now(UTC)


# --- Tables (in FK-dependency order) ------------------------------------------


class User(SQLModel, table=True):
    __tablename__ = "users"

    slug: str = Field(primary_key=True)
    role: UserRole = Field(sa_column=Column(SAEnum(UserRole, native_enum=False), nullable=False))
    managed: bool = Field(default=True)


class App(SQLModel, table=True):
    __tablename__ = "apps"

    slug: str = Field(primary_key=True)
    exe_paths: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    process_names: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    urls: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))


class Device(SQLModel, table=True):
    __tablename__ = "devices"

    slug: str = Field(primary_key=True)
    owner: str | None = Field(default=None, foreign_key="users.slug")
    type: DeviceType = Field(
        sa_column=Column(SAEnum(DeviceType, native_enum=False), nullable=False)
    )
    os: DeviceOS = Field(sa_column=Column(SAEnum(DeviceOS, native_enum=False), nullable=False))
    mac: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    managed: bool = Field(default=True)


class Agent(SQLModel, table=True):
    __tablename__ = "agents"

    device: str = Field(primary_key=True, foreign_key="devices.slug")
    type: str
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    last_heartbeat: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))
    last_seen_version: str | None = None


class AgentToken(SQLModel, table=True):
    __tablename__ = "agent_tokens"
    __table_args__ = (
        UniqueConstraint("token_hash"),
        Index("ix_agent_tokens_device_revoked_at", "device", "revoked_at"),
    )

    id: UUID = Field(default_factory=uuid4, primary_key=True)
    token_hash: str
    device: str = Field(foreign_key="devices.slug")
    created_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )
    revoked_at: datetime | None = Field(default=None, sa_column=Column(UTCDateTime))


class Plugin(SQLModel, table=True):
    __tablename__ = "plugins"

    type: str = Field(primary_key=True)
    instance_id: str = Field(default="default", primary_key=True)
    config: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    users: list[str] = Field(default_factory=list, sa_column=Column(JSON, nullable=False))
    paused: bool = Field(default=False)


class UserLock(SQLModel, table=True):
    __tablename__ = "user_locks"

    user: str = Field(primary_key=True, foreign_key="users.slug")
    manual_lock: bool = Field(default=False)
    set_at: datetime = Field(default_factory=_utcnow, sa_column=Column(UTCDateTime, nullable=False))
    set_by: str | None = None


class AuditLog(SQLModel, table=True):
    __tablename__ = "audit_log"
    __table_args__ = (
        Index("ix_audit_log_occurred_at", "occurred_at"),
        Index("ix_audit_log_target_kind_target_id", "target_kind", "target_id"),
    )

    id: int | None = Field(default=None, primary_key=True)
    actor: str
    action: str
    target_kind: AuditTargetKind = Field(
        sa_column=Column(SAEnum(AuditTargetKind, native_enum=False), nullable=False)
    )
    target_id: str
    payload: dict[str, Any] = Field(default_factory=dict, sa_column=Column(JSON, nullable=False))
    occurred_at: datetime = Field(
        default_factory=_utcnow, sa_column=Column(UTCDateTime, nullable=False)
    )


class Manifest(SQLModel, table=True):
    __tablename__ = "manifests"

    type: str = Field(primary_key=True)
    version: str
    sha256: str
    url: str


class Settings(SQLModel, table=True):
    __tablename__ = "settings"
    __table_args__ = (CheckConstraint("id = 1", name="singleton"),)

    id: int | None = Field(default=1, primary_key=True)
    agent_tick_seconds: int = Field(default=60)
    manifest_tick_seconds: int = Field(default=3600)
    plugin_resync_seconds: int = Field(default=300)
    plugin_reconcile_timeout_seconds: int = Field(default=30)
    audit_retention_days: int = Field(default=90)
