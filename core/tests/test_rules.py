"""Tests for the lock-status rule pipeline.

Covers the kernel's reference rule (``manual_lock``) and the pipeline
machinery: registration, OR composition, and the ``managed=False`` skip.
"""

from __future__ import annotations

from collections.abc import Iterator

import pytest
from curfew.models import User, UserLock, UserRole
from curfew.rules import RulePipeline, manual_lock_rule, register_kernel_rules, user_scope
from curfew.schemas import Reason
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


@pytest.fixture
def session(tmp_path) -> Iterator[Session]:
    db = tmp_path / "test.sqlite"
    engine = create_engine(f"sqlite:///{db}")
    SQLModel.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


@pytest.fixture
def kernel_pipeline() -> Iterator[None]:
    """Register the kernel's rules; reset on teardown so tests don't leak."""
    register_kernel_rules()
    yield
    user_scope.reset()


def _add_user(session: Session, *, username: str, managed: bool = True) -> User:
    u = User(username=username, role=UserRole.MEMBER, managed=managed)
    session.add(u)
    session.flush()
    return u


def test_manual_lock_rule_returns_none_when_no_lock_row(session: Session) -> None:
    _add_user(session, username="kid1")
    assert manual_lock_rule(session, "kid1") is None


def test_manual_lock_rule_returns_none_when_lock_false(session: Session) -> None:
    user = _add_user(session, username="kid1")
    session.add(UserLock(user_id=user.id, manual_lock=False))
    session.flush()
    assert manual_lock_rule(session, "kid1") is None


def test_manual_lock_rule_returns_reason_when_lock_true(session: Session) -> None:
    user = _add_user(session, username="kid1")
    session.add(UserLock(user_id=user.id, manual_lock=True))
    session.flush()
    reason = manual_lock_rule(session, "kid1")
    assert reason == Reason(kind="manual_lock")


def test_manual_lock_rule_skips_unmanaged(session: Session) -> None:
    user = _add_user(session, username="adult", managed=False)
    session.add(UserLock(user_id=user.id, manual_lock=True))
    session.flush()
    # managed=False means lock rules don't apply (PLAN.md §"Users").
    assert manual_lock_rule(session, "adult") is None


def test_manual_lock_rule_returns_none_for_missing_user(session: Session) -> None:
    assert manual_lock_rule(session, "ghost") is None


def test_pipeline_locked_is_or_of_rules(session: Session) -> None:
    """Pipeline reports locked iff *any* registered rule fires."""
    pipeline = RulePipeline()

    def yes_rule(_session, _target):
        return Reason(kind="yes")

    def no_rule(_session, _target):
        return None

    pipeline.register(no_rule)
    status = pipeline.evaluate(session, "anything")
    assert status.locked is False
    assert status.reasons == []

    pipeline.register(yes_rule)
    status = pipeline.evaluate(session, "anything")
    assert status.locked is True
    assert status.reasons == [Reason(kind="yes")]


def test_pipeline_collects_all_firing_reasons(session: Session) -> None:
    pipeline = RulePipeline()
    pipeline.register(lambda _s, _t: Reason(kind="a"))
    pipeline.register(lambda _s, _t: None)
    pipeline.register(lambda _s, _t: Reason(kind="b"))

    status = pipeline.evaluate(session, "x")
    assert status.locked is True
    assert [r.kind for r in status.reasons] == ["a", "b"]


def test_kernel_pipeline_registers_manual_lock(session: Session, kernel_pipeline: None) -> None:
    user = _add_user(session, username="kid1")
    session.add(UserLock(user_id=user.id, manual_lock=True))
    session.flush()

    status = user_scope.evaluate(session, "kid1")
    assert status.locked is True
    assert [r.kind for r in status.reasons] == ["manual_lock"]


def test_reason_allows_extra_fields() -> None:
    """Per PLAN.md, reasons can carry rule-specific detail beyond kind."""
    r = Reason(kind="budget_exhausted", consumed=120, budget=90)
    assert r.kind == "budget_exhausted"
    assert r.model_dump() == {"kind": "budget_exhausted", "consumed": 120, "budget": 90}
