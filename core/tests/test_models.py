"""Tests for core/curfew/models.py.

Uses SQLAlchemy create_all directly (not Alembic) so this exercises the model
definitions in isolation. The migration is exercised separately in
api/tests/test_migration.py.
"""

from __future__ import annotations

from collections.abc import Iterator
from datetime import UTC, datetime

import pytest
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
    Settings,
    User,
    UserLock,
    UserRole,
)
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlmodel import Session, SQLModel, create_engine, select


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection, _connection_record):
    """SQLite ignores FKs by default; turn enforcement on for tests."""
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


def test_user_round_trip(session: Session) -> None:
    session.add(User(username="kid1", role=UserRole.MEMBER, managed=True))
    session.commit()
    got = session.exec(select(User).where(User.username == "kid1")).one()
    assert got.id is not None
    assert got.username == "kid1"
    assert got.role is UserRole.MEMBER
    assert got.managed is True


def test_username_unique(session: Session) -> None:
    session.add(User(username="kid1", role=UserRole.MEMBER))
    session.commit()
    session.add(User(username="kid1", role=UserRole.MEMBER))
    with pytest.raises(IntegrityError):
        session.commit()


def test_app_json_columns(session: Session) -> None:
    session.add(
        App(
            slug="steam",
            exe_paths=["C:/Steam/steam.exe"],
            process_names=["steam.exe"],
            urls=["https://store.steampowered.com"],
        )
    )
    session.commit()
    got = session.exec(select(App).where(App.slug == "steam")).one()
    assert got.id is not None
    assert got.exe_paths == ["C:/Steam/steam.exe"]
    assert got.urls == ["https://store.steampowered.com"]


def test_device_fk_to_user(session: Session) -> None:
    user = User(username="kid1", role=UserRole.MEMBER)
    session.add(user)
    session.flush()
    session.add(
        Device(
            slug="gamingrig",
            owner_id=user.id,
            type=DeviceType.PC,
            os=DeviceOS.WINDOWS,
            mac=["aa:bb:cc:dd:ee:ff"],
        )
    )
    session.commit()
    got = session.exec(select(Device).where(Device.slug == "gamingrig")).one()
    assert got.owner_id == user.id
    assert got.type is DeviceType.PC


def test_device_owner_nullable_for_shared(session: Session) -> None:
    session.add(Device(slug="livingroomtv", owner_id=None, type=DeviceType.TV, os=DeviceOS.ANDROID))
    session.commit()
    got = session.exec(select(Device).where(Device.slug == "livingroomtv")).one()
    assert got.owner_id is None


def test_device_fk_violation_rejects_unknown_owner(session: Session) -> None:
    session.add(Device(slug="d1", owner_id=999, type=DeviceType.PC, os=DeviceOS.WINDOWS))
    with pytest.raises(IntegrityError):
        session.commit()


def test_agent_one_per_device(session: Session) -> None:
    user = User(username="kid1", role=UserRole.MEMBER)
    session.add(user)
    session.flush()
    device = Device(slug="rig", owner_id=user.id, type=DeviceType.PC, os=DeviceOS.WINDOWS)
    session.add(device)
    session.flush()
    session.add(
        Agent(device_id=device.id, type="windows-agent", config={"windows_user": "Kid1Local"})
    )
    session.commit()

    # Trying to add a second agent for the same device violates PK.
    session.add(Agent(device_id=device.id, type="other-agent", config={}))
    with pytest.raises(IntegrityError):
        session.commit()


def test_agent_token_unique_hash(session: Session) -> None:
    user = User(username="kid1", role=UserRole.MEMBER)
    session.add(user)
    session.flush()
    device = Device(slug="rig", owner_id=user.id, type=DeviceType.PC, os=DeviceOS.WINDOWS)
    session.add(device)
    session.flush()
    t1 = AgentToken(token_hash="hash-a", device_id=device.id)
    t2 = AgentToken(token_hash="hash-b", device_id=device.id)
    session.add_all([t1, t2])
    session.commit()
    assert t1.id is not None and t2.id is not None and t1.id != t2.id

    # Second token with the same hash violates UNIQUE.
    session.add(AgentToken(token_hash="hash-a", device_id=device.id))
    with pytest.raises(IntegrityError):
        session.commit()


def test_plugin_composite_pk(session: Session) -> None:
    session.add(Plugin(type="adguard", config={"url": "x"}, users=["*"]))
    session.add(Plugin(type="smart_plug", instance_id="livingroom", users=["kid1"]))
    session.add(Plugin(type="smart_plug", instance_id="bedroom", users=["kid2"]))
    session.commit()

    rows = session.exec(select(Plugin)).all()
    assert {(r.type, r.instance_id) for r in rows} == {
        ("adguard", "default"),
        ("smart_plug", "livingroom"),
        ("smart_plug", "bedroom"),
    }


def test_user_lock_default_unset(session: Session) -> None:
    user = User(username="kid1", role=UserRole.MEMBER)
    session.add(user)
    session.flush()
    session.add(UserLock(user_id=user.id))
    session.commit()
    got = session.exec(select(UserLock).where(UserLock.user_id == user.id)).one()
    assert got.manual_lock is False
    assert got.set_at.tzinfo is not None


def test_audit_log_target_kind_enum(session: Session) -> None:
    session.add(
        AuditLog(
            actor="operator",
            action="user.lock",
            target_kind=AuditTargetKind.USER,
            target_id="kid1",
            payload={"reason": "manual"},
        )
    )
    session.commit()
    got = session.exec(select(AuditLog)).one()
    assert got.target_kind is AuditTargetKind.USER
    assert got.payload == {"reason": "manual"}


def test_manifest_round_trip(session: Session) -> None:
    session.add(
        Manifest(type="windows-agent", version="1.0.0", sha256="deadbeef", url="https://x/y")
    )
    session.commit()
    got = session.exec(select(Manifest).where(Manifest.type == "windows-agent")).one()
    assert got.url == "https://x/y"


def test_settings_singleton_check(session: Session) -> None:
    session.add(Settings(id=1))
    session.commit()
    session.add(Settings(id=2))
    with pytest.raises(IntegrityError):
        session.commit()


def test_settings_defaults(session: Session) -> None:
    session.add(Settings(id=1))
    session.commit()
    got = session.exec(select(Settings)).one()
    assert got.agent_tick_seconds == 60
    assert got.manifest_tick_seconds == 3600
    assert got.plugin_resync_seconds == 300
    assert got.plugin_reconcile_timeout_seconds == 30
    assert got.audit_retention_days == 90


def test_datetime_round_trip_is_utc(session: Session) -> None:
    user = User(username="kid1", role=UserRole.MEMBER)
    session.add(user)
    session.flush()
    when = datetime(2026, 5, 5, 12, 0, tzinfo=UTC)
    session.add(UserLock(user_id=user.id, manual_lock=True, set_at=when))
    session.commit()
    got = session.exec(select(UserLock)).one()
    assert got.set_at == when
    assert got.set_at.tzinfo is not None


def test_naive_datetime_rejected(session: Session) -> None:
    from sqlalchemy.exc import StatementError

    user = User(username="kid1", role=UserRole.MEMBER)
    session.add(user)
    session.flush()
    session.add(UserLock(user_id=user.id, set_at=datetime(2026, 5, 5, 12, 0)))
    with pytest.raises(StatementError, match="naive datetime rejected"):
        session.commit()
