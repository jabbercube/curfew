"""curfew shared library.

The ``Settings`` name is intentionally NOT re-exported at this top level — it
collides between two valid concepts: the boot-time ``Settings`` BaseSettings in
``curfew.config`` and the runtime ``Settings`` SQLModel row in ``curfew.models``.
Always import explicitly from one of those modules.
"""

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

__all__ = [
    "Agent",
    "AgentToken",
    "App",
    "AuditLog",
    "AuditTargetKind",
    "Device",
    "DeviceOS",
    "DeviceType",
    "Manifest",
    "Plugin",
    "User",
    "UserLock",
    "UserRole",
    "get_engine",
    "get_session",
    "make_engine",
    "reset_engine_cache",
]
