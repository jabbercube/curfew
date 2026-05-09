"""Tests for PluginRuntime — the in-memory instance manager + dispatch.

These tests use synthetic plugin classes built directly in the test (no
discovery, no manifest parsing — that's covered in test_plugin_loader.py).
A small fake ``PluginRegistry`` lets us bind a hand-rolled plugin class to
a type name and exercise the runtime's lifecycle methods without touching
the filesystem.

Tests are sync wrappers around ``asyncio.run`` for symmetry with
``test_plugin_sdk.py`` — pytest-asyncio isn't a project dep and the small
amount of boilerplate avoids adding one for these few tests.
"""

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any, ClassVar

import pytest
from curfew.models import AuditLog, Device, DeviceOS, DeviceType, User, UserLock, UserRole
from curfew.models import Settings as SettingsRow
from curfew.plugin import Plugin, ReconcileResult, Scope
from curfew.plugin_loader import PluginManifest, PluginRegistry, PluginType
from curfew.plugin_runtime import (
    AlreadyAssignedError,
    NotAssignedError,
    PluginInstantiationError,
    PluginRuntime,
    UnknownPluginTypeError,
)
from curfew.rules import register_kernel_rules, user_scope
from pydantic import BaseModel, ValidationError
from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine, select


@event.listens_for(Engine, "connect")
def _enable_sqlite_foreign_keys(dbapi_connection: Any, _connection_record: Any) -> None:
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.close()


@pytest.fixture
def engine(tmp_path: Path) -> Iterator[Engine]:
    db = tmp_path / "test.sqlite"
    eng = create_engine(f"sqlite:///{db}")
    SQLModel.metadata.create_all(eng)
    # Seed the singleton settings row so _read_timeout finds something.
    with Session(eng) as s:
        s.add(SettingsRow(id=1))
        s.commit()
    yield eng


@pytest.fixture
def kernel_rules() -> Iterator[None]:
    register_kernel_rules()
    yield
    user_scope.reset()


# --- Plugin fixtures ---------------------------------------------------------


class _Cfg(BaseModel):
    note: str = "hello"


class _Recorder(Plugin):
    """Plugin that records every reconcile call onto a class-level list."""

    calls: ClassVar[list[Scope]] = []
    init_count: ClassVar[int] = 0

    def __init__(self, config: _Cfg) -> None:
        self.config = config
        type(self).init_count += 1

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        type(self).calls.append(scope)
        return ReconcileResult.ok()


class _Slow(Plugin):
    def __init__(self, config: _Cfg) -> None:
        self.config = config

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        await asyncio.sleep(60)
        return ReconcileResult.ok()


class _Boom(Plugin):
    def __init__(self, config: _Cfg) -> None:
        self.config = config

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        raise RuntimeError("kaboom")


class _ReturnsError(Plugin):
    def __init__(self, config: _Cfg) -> None:
        self.config = config

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        return ReconcileResult.error("upstream 500")


class _BadInit(Plugin):
    def __init__(self, config: _Cfg) -> None:
        raise ValueError("init exploded")

    async def reconcile(self, scope: Scope) -> ReconcileResult:
        return ReconcileResult.ok()


def _ptype(name: str, cls: type[Plugin]) -> PluginType:
    return PluginType(
        type=name,
        folder=Path(f"/fake/{name}"),
        manifest=PluginManifest(
            type=name, name=name, version="1.0", description=name, config_schema="Config"
        ),
        plugin_class=cls,
        config_model=_Cfg,
        error=None,
    )


@pytest.fixture
def registry() -> PluginRegistry:
    r = PluginRegistry()
    r.add(_ptype("recorder", _Recorder))
    r.add(_ptype("slow", _Slow))
    r.add(_ptype("boom", _Boom))
    r.add(_ptype("err", _ReturnsError))
    r.add(_ptype("bad_init", _BadInit))
    r.add(
        PluginType(
            type="halfbroken",
            folder=Path("/fake/halfbroken"),
            manifest=None,
            plugin_class=None,
            config_model=None,
            error="manifest.toml: missing required field(s): config_schema",
        )
    )
    return r


@pytest.fixture(autouse=True)
def reset_recorder_state() -> None:
    _Recorder.calls = []
    _Recorder.init_count = 0


@pytest.fixture
def runtime(registry: PluginRegistry, engine: Engine) -> PluginRuntime:
    return PluginRuntime(registry=registry, engine=engine)


def _seed_user(
    engine: Engine, username: str, *, locked: bool = False, managed: bool = True
) -> None:
    with Session(engine) as s:
        u = User(username=username, role=UserRole.MEMBER, managed=managed)
        s.add(u)
        s.flush()
        if locked:
            s.add(UserLock(user_id=u.id, manual_lock=True))
        s.commit()


def _seed_device(
    engine: Engine,
    *,
    owner: str,
    slug: str,
    mac: list[str],
    type_: DeviceType = DeviceType.PC,
    os_: DeviceOS = DeviceOS.WINDOWS,
    managed: bool = True,
) -> None:
    with Session(engine) as s:
        u = s.exec(select(User).where(User.username == owner)).one()
        s.add(
            Device(
                slug=slug,
                owner_id=u.id,
                type=type_,
                os=os_,
                mac=mac,
                managed=managed,
            )
        )
        s.commit()


# --- build_instance ----------------------------------------------------------


def test_build_unknown_type(runtime: PluginRuntime) -> None:
    with pytest.raises(UnknownPluginTypeError):
        runtime.build_instance("does_not_exist", {})


def test_build_failed_type(runtime: PluginRuntime) -> None:
    """A discovered-but-failed type can't be built either."""
    with pytest.raises(UnknownPluginTypeError):
        runtime.build_instance("halfbroken", {})


def test_build_invalid_config(runtime: PluginRuntime) -> None:
    """A config that doesn't match the Pydantic model raises ValidationError."""
    with pytest.raises(ValidationError):
        runtime.build_instance("recorder", {"note": 123})


def test_build_init_raises(runtime: PluginRuntime) -> None:
    with pytest.raises(PluginInstantiationError, match="init exploded"):
        runtime.build_instance("bad_init", {})


# --- register / unassign / update -------------------------------------------


def test_register_and_unassign(runtime: PluginRuntime) -> None:
    async def go() -> None:
        inst = runtime.build_instance("recorder", {})
        live = await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=inst,
            config={"note": "hello"},
            users=["kid1"],
        )
        assert live.type == "recorder"
        assert live.instance is inst
        assert runtime.get("recorder", "default") is live

        await runtime.unassign(type_name="recorder", instance_id="default")
        assert runtime.get("recorder", "default") is None

    asyncio.run(go())


def test_register_duplicate_raises(runtime: PluginRuntime) -> None:
    async def go() -> None:
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=runtime.build_instance("recorder", {}),
            config={},
            users=[],
        )
        with pytest.raises(AlreadyAssignedError):
            await runtime.register(
                type_name="recorder",
                instance_id="default",
                instance=runtime.build_instance("recorder", {}),
                config={},
                users=[],
            )

    asyncio.run(go())


def test_unassign_idempotent(runtime: PluginRuntime) -> None:
    """Unassigning a slot that isn't there is a no-op (no exception)."""
    asyncio.run(runtime.unassign(type_name="recorder", instance_id="default"))


def test_update_unknown_raises(runtime: PluginRuntime) -> None:
    async def go() -> None:
        with pytest.raises(NotAssignedError):
            await runtime.update(type_name="recorder", instance_id="default")

    asyncio.run(go())


def test_update_users_doesnt_reinstantiate(runtime: PluginRuntime) -> None:
    async def go() -> None:
        inst = runtime.build_instance("recorder", {})
        baseline = _Recorder.init_count
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=inst,
            config={},
            users=["kid1"],
        )
        live = await runtime.update(
            type_name="recorder", instance_id="default", users=["kid1", "kid2"]
        )
        assert live.users == ["kid1", "kid2"]
        assert live.instance is inst
        assert _Recorder.init_count == baseline

    asyncio.run(go())


def test_update_enabled_doesnt_reinstantiate(runtime: PluginRuntime) -> None:
    async def go() -> None:
        inst = runtime.build_instance("recorder", {})
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=inst,
            config={},
            users=["*"],
        )
        baseline = _Recorder.init_count
        live = await runtime.update(type_name="recorder", instance_id="default", enabled=False)
        assert live.enabled is False
        assert live.instance is inst
        assert _Recorder.init_count == baseline

    asyncio.run(go())


def test_update_config_reinstantiates(runtime: PluginRuntime) -> None:
    async def go() -> None:
        inst = runtime.build_instance("recorder", {"note": "first"})
        baseline = _Recorder.init_count
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=inst,
            config={"note": "first"},
            users=["*"],
        )
        live = await runtime.update(
            type_name="recorder", instance_id="default", config={"note": "second"}
        )
        assert live.config == {"note": "second"}
        assert live.instance is not inst
        assert _Recorder.init_count == baseline + 1

    asyncio.run(go())


def test_update_config_unchanged_skips_reinstantiate(runtime: PluginRuntime) -> None:
    async def go() -> None:
        inst = runtime.build_instance("recorder", {"note": "x"})
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=inst,
            config={"note": "x"},
            users=["*"],
        )
        baseline = _Recorder.init_count
        live = await runtime.update(
            type_name="recorder", instance_id="default", config={"note": "x"}
        )
        assert live.instance is inst
        assert _Recorder.init_count == baseline

    asyncio.run(go())


# --- dispatch_for_user -------------------------------------------------------


def test_dispatch_picks_governing_instances(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    _seed_user(engine, "kid1", locked=True)

    async def go() -> None:
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=runtime.build_instance("recorder", {}),
            config={},
            users=["kid1"],
        )
        await runtime.dispatch_for_user("kid1")

    asyncio.run(go())

    assert len(_Recorder.calls) == 1
    scope = _Recorder.calls[0]
    assert scope.user == "kid1"
    assert scope.locked is True
    assert any(r.kind == "manual_lock" for r in scope.reasons)


def test_dispatch_skips_non_governing(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    _seed_user(engine, "kid1")
    _seed_user(engine, "kid2")

    async def go() -> None:
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=runtime.build_instance("recorder", {}),
            config={},
            users=["kid2"],
        )
        await runtime.dispatch_for_user("kid1")

    asyncio.run(go())
    assert _Recorder.calls == []


def test_dispatch_wildcard_users(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    _seed_user(engine, "anyone")

    async def go() -> None:
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=runtime.build_instance("recorder", {}),
            config={},
            users=["*"],
        )
        await runtime.dispatch_for_user("anyone")

    asyncio.run(go())
    assert len(_Recorder.calls) == 1


def test_dispatch_populates_scope_devices(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    """``Scope.devices`` carries the user's devices, ordered by slug."""
    _seed_user(engine, "kid1", locked=True)
    _seed_device(engine, owner="kid1", slug="kid1-phone", mac=["aa:bb:cc:dd:ee:01"])
    _seed_device(
        engine,
        owner="kid1",
        slug="kid1-laptop",
        mac=["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:03"],
        type_=DeviceType.PC,
        os_=DeviceOS.WINDOWS,
    )

    async def go() -> None:
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=runtime.build_instance("recorder", {}),
            config={},
            users=["*"],
        )
        await runtime.dispatch_for_user("kid1")

    asyncio.run(go())

    scope = _Recorder.calls[0]
    assert [d.slug for d in scope.devices] == ["kid1-laptop", "kid1-phone"]
    laptop = scope.devices[0]
    assert laptop.mac == ["aa:bb:cc:dd:ee:02", "aa:bb:cc:dd:ee:03"]
    assert laptop.type == DeviceType.PC
    assert laptop.os == DeviceOS.WINDOWS
    assert laptop.managed is True


def test_dispatch_devices_empty_when_user_has_none(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    _seed_user(engine, "kid1")

    async def go() -> None:
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=runtime.build_instance("recorder", {}),
            config={},
            users=["*"],
        )
        await runtime.dispatch_for_user("kid1")

    asyncio.run(go())

    assert _Recorder.calls[0].devices == []


def test_dispatch_devices_excludes_other_users(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    """A device owned by kid2 must not show up in kid1's scope."""
    _seed_user(engine, "kid1")
    _seed_user(engine, "kid2")
    _seed_device(engine, owner="kid1", slug="kid1-pc", mac=["aa:bb:cc:dd:ee:01"])
    _seed_device(engine, owner="kid2", slug="kid2-pc", mac=["aa:bb:cc:dd:ee:02"])

    async def go() -> None:
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=runtime.build_instance("recorder", {}),
            config={},
            users=["*"],
        )
        await runtime.dispatch_for_user("kid1")

    asyncio.run(go())

    assert [d.slug for d in _Recorder.calls[0].devices] == ["kid1-pc"]


def test_dispatch_skips_disabled(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    _seed_user(engine, "kid1")

    async def go() -> None:
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=runtime.build_instance("recorder", {}),
            config={},
            users=["*"],
            enabled=False,
        )
        await runtime.dispatch_for_user("kid1")

    asyncio.run(go())
    assert _Recorder.calls == []


def test_dispatch_audits_timeout(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    """A reconcile that exceeds the timeout is cancelled + audited."""
    with Session(engine) as s:
        row = s.exec(select(SettingsRow)).one()
        row.plugin_reconcile_timeout_seconds = 1
        s.commit()

    _seed_user(engine, "kid1")

    async def go() -> None:
        await runtime.register(
            type_name="slow",
            instance_id="default",
            instance=runtime.build_instance("slow", {}),
            config={},
            users=["*"],
        )
        await runtime.dispatch_for_user("kid1")

    asyncio.run(go())

    with Session(engine) as s:
        rows = list(s.exec(select(AuditLog).where(AuditLog.action == "plugin.reconcile_failed")))
    assert len(rows) == 1
    assert rows[0].target_id == "slow/default"
    assert "timed out" in rows[0].payload["error"]


def test_dispatch_audits_exception(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    _seed_user(engine, "kid1")

    async def go() -> None:
        await runtime.register(
            type_name="boom",
            instance_id="default",
            instance=runtime.build_instance("boom", {}),
            config={},
            users=["*"],
        )
        await runtime.dispatch_for_user("kid1")

    asyncio.run(go())

    with Session(engine) as s:
        rows = list(s.exec(select(AuditLog).where(AuditLog.action == "plugin.reconcile_failed")))
    assert len(rows) == 1
    assert "RuntimeError" in rows[0].payload["error"]
    assert "kaboom" in rows[0].payload["error"]


def test_dispatch_audits_error_result(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    _seed_user(engine, "kid1")

    async def go() -> None:
        await runtime.register(
            type_name="err",
            instance_id="default",
            instance=runtime.build_instance("err", {}),
            config={},
            users=["*"],
        )
        await runtime.dispatch_for_user("kid1")

    asyncio.run(go())

    with Session(engine) as s:
        rows = list(s.exec(select(AuditLog).where(AuditLog.action == "plugin.reconcile_failed")))
    assert len(rows) == 1
    assert "upstream 500" in rows[0].payload["error"]


# --- safety_net_resync -------------------------------------------------------


def test_resync_walks_managed_users(
    runtime: PluginRuntime, engine: Engine, kernel_rules: None
) -> None:
    _seed_user(engine, "kid1", locked=True)
    _seed_user(engine, "kid2")
    _seed_user(engine, "guest", managed=False)

    async def go() -> None:
        await runtime.register(
            type_name="recorder",
            instance_id="default",
            instance=runtime.build_instance("recorder", {}),
            config={},
            users=["*"],
        )
        await runtime.safety_net_resync()

    asyncio.run(go())

    seen = sorted({s.user for s in _Recorder.calls})
    assert seen == ["kid1", "kid2"]
