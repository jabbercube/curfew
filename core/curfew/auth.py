"""Actor value object — the authenticated principal behind an API call.

Two flavors:

- **root**: the request was authenticated via the operator root token. There's
  no underlying user row; we synthesise ``role=ADMIN`` so role-gating deps
  treat root the same as any admin user. ``audit_str`` returns ``"operator"``
  so audit history doesn't fork pre/post per-user-auth.
- **user**: the request was authenticated via a session cookie. ``user_id``,
  ``username``, and ``role`` come from the corresponding ``users`` row.

Lives in ``core`` (not ``api``) so audit + future SDK consumers can build
``Actor`` instances without depending on FastAPI.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from curfew.models import UserRole


@dataclass(frozen=True, slots=True)
class Actor:
    kind: Literal["root", "user"]
    role: UserRole
    user_id: int | None = None
    username: str | None = None

    @property
    def audit_str(self) -> str:
        """The string written to ``audit_log.actor`` for this actor."""
        if self.kind == "root":
            return "operator"
        # Session-authenticated user — username is always set.
        assert self.username is not None
        return self.username

    def has_role(self, minimum: UserRole) -> bool:
        """True if this actor's role is at least ``minimum``.

        Hierarchy: ``MEMBER`` < ``MANAGER`` < ``ADMIN``.
        """
        return _ROLE_RANK[self.role] >= _ROLE_RANK[minimum]


_ROLE_RANK: dict[UserRole, int] = {
    UserRole.MEMBER: 0,
    UserRole.MANAGER: 1,
    UserRole.ADMIN: 2,
}


def root_actor() -> Actor:
    """Synthetic actor for root-token-authenticated requests."""
    return Actor(kind="root", role=UserRole.ADMIN)
