"""Audit log helper.

Per PLAN.md §"Audit log": every API write records ``(actor, action, target,
payload, occurred_at)`` to the ``audit_log`` table. PLAN.md describes this as
"a single audit middleware"; the implementation is a single helper function
called from each write route. Same outcome — every write is audited the same
way without per-route conditional logic — without the ASGI-level magic of path
parsing to derive ``target_kind``/``target_id``. Each route knows its own
target; the helper just records.

The helper commits the audit row in the same transaction the route's write
participates in, so a write that rolls back also rolls back its audit. The
caller flushes; the helper does not commit on its own.
"""

from __future__ import annotations

from typing import Any

from sqlmodel import Session

from curfew.models import AuditLog, AuditTargetKind


def record_audit(
    session: Session,
    *,
    actor: str,
    action: str,
    target_kind: AuditTargetKind,
    target_id: str,
    payload: dict[str, Any] | None = None,
) -> AuditLog:
    """Write one audit row to ``audit_log`` and return it.

    Does not call ``session.commit()`` — the caller owns the transaction so
    the audit row commits atomically with the write it describes (or rolls
    back together if the route fails).

    Args:
        actor: Identifier of who initiated the change. ``"operator"`` for V1
            (root token); future per-user auth uses the username.
        action: Dotted verb naming the operation (e.g. ``"user.lock"``,
            ``"user.create"``). Caller's responsibility — keep it consistent
            so audit queries by ``action`` work.
        target_kind: Which kind of object the change affects.
        target_id: The target's username/slug at the time of the event. Stays
            a string so audit values survive deletion of the target (PLAN.md).
        payload: Action-specific JSON-serialisable detail.
    """
    row = AuditLog(
        actor=actor,
        action=action,
        target_kind=target_kind,
        target_id=target_id,
        payload=payload or {},
    )
    session.add(row)
    session.flush()
    return row
