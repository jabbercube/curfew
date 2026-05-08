"""curfew shared library.

The ``Settings`` name is intentionally NOT re-exported at this top level — it
collides between two valid concepts: the boot-time ``Settings`` BaseSettings in
``curfew.config`` and the runtime ``Settings`` SQLModel row in ``curfew.models``.
Always import explicitly from one of those modules.
"""

from curfew.audit import record_audit
from curfew.db import get_engine, get_session, make_engine, reset_engine_cache
from curfew.models import (
    Agent,
    AgentToken,
    App,
    AuditLog,
    AuditTargetKind,
    Device,
    DeviceOS,
    DeviceType,
    Manifest,
    Plugin,
    User,
    UserLock,
    UserRole,
)
from curfew.rules import (
    RulePipeline,
    device_scope,
    manual_lock_rule,
    register_kernel_rules,
    user_scope,
)
from curfew.schemas import (
    AgentSnapshotRow,
    AppCreate,
    AppRead,
    AppUpdate,
    DeviceCreate,
    DeviceRead,
    DeviceUpdate,
    LockStatus,
    ManifestSnapshotRow,
    PluginSnapshotRow,
    Reason,
    SettingsRead,
    SettingsUpdate,
    SystemSnapshot,
    UserCreate,
    UserLockSnapshotRow,
    UserRead,
    UserUpdate,
)

__all__ = [
    "Agent",
    "AgentSnapshotRow",
    "AgentToken",
    "App",
    "AppCreate",
    "AppRead",
    "AppUpdate",
    "AuditLog",
    "AuditTargetKind",
    "Device",
    "DeviceCreate",
    "DeviceOS",
    "DeviceRead",
    "DeviceType",
    "DeviceUpdate",
    "LockStatus",
    "Manifest",
    "ManifestSnapshotRow",
    "Plugin",
    "PluginSnapshotRow",
    "Reason",
    "RulePipeline",
    "SettingsRead",
    "SettingsUpdate",
    "SystemSnapshot",
    "User",
    "UserCreate",
    "UserLock",
    "UserLockSnapshotRow",
    "UserRead",
    "UserRole",
    "UserUpdate",
    "device_scope",
    "get_engine",
    "get_session",
    "make_engine",
    "manual_lock_rule",
    "record_audit",
    "register_kernel_rules",
    "reset_engine_cache",
    "user_scope",
]
