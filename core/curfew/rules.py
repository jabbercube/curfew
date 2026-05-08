"""Lock-status rule pipeline.

Per PLAN.md §"Lock status rule pipeline" and ADR-008. The kernel computes lock
status per scope; two scopes ship in the kernel:

- **User scope** — ``GET /v1/users/{user}/status`` runs every user-scope rule.
- **Device scope** — ``GET /v1/devices/{device}/status`` runs every device-scope
  rule. Empty in the kernel; the shared-device-lock feature (a future PR) is
  the first device-scope rule to register.

Rules are callables ``(session, target_id) -> Reason | None``. ``locked`` is
the boolean OR of registered rules' outputs; ``reasons`` is the list of
non-``None`` returns. Adding a rule is registering a callable into the right
scope's pipeline — no API surface changes, no central if/else to edit.

The kernel ships with **one rule: ``manual_lock``** (user scope). It returns
``Reason(kind="manual_lock")`` when the user's ``user_locks.manual_lock`` flag
is set, and skips users with ``managed=False`` (PLAN.md §"Users").
"""

from __future__ import annotations

from collections.abc import Callable

from sqlmodel import Session, select

from curfew.models import User, UserLock
from curfew.schemas import LockStatus, Reason

Rule = Callable[[Session, str], Reason | None]


class RulePipeline:
    """A scoped pipeline of rules. ``evaluate`` ORs all registered rules."""

    def __init__(self) -> None:
        self._rules: list[Rule] = []

    def register(self, rule: Rule) -> None:
        self._rules.append(rule)

    def reset(self) -> None:
        """Clear registered rules. Test-only."""
        self._rules.clear()

    def evaluate(self, session: Session, target_id: str) -> LockStatus:
        reasons: list[Reason] = []
        for rule in self._rules:
            r = rule(session, target_id)
            if r is not None:
                reasons.append(r)
        return LockStatus(locked=bool(reasons), reasons=reasons)


user_scope = RulePipeline()
device_scope = RulePipeline()


def manual_lock_rule(session: Session, username: str) -> Reason | None:
    """Return ``Reason(kind="manual_lock")`` if the user is manually locked.

    Returns ``None`` (not locked by this rule) when:
    - the user does not exist (status endpoint returns 404 separately),
    - the user is ``managed=False`` (per PLAN.md, lock rules don't apply),
    - the user has no ``user_locks`` row, or
    - the user's ``user_locks.manual_lock`` is False.
    """
    user = session.exec(select(User).where(User.username == username)).first()
    if user is None or not user.managed:
        return None
    lock = session.exec(select(UserLock).where(UserLock.user_id == user.id)).first()
    if lock is not None and lock.manual_lock:
        return Reason(kind="manual_lock")
    return None


def register_kernel_rules() -> None:
    """Register the rules that ship with the kernel.

    Called by ``api.curfew_api.app.create_app()`` at startup. Idempotent: clears
    each scope first, so calling it twice (e.g. in tests) doesn't stack rules.
    """
    user_scope.reset()
    device_scope.reset()
    user_scope.register(manual_lock_rule)
