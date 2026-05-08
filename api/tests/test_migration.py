"""Test the initial Alembic migration end-to-end.

Apply 0001_initial against a temp SQLite DB; reflect the schema; assert that
all 10 kernel tables exist with the documented constraints, and that the
seeded singleton settings row holds the documented defaults.
"""

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

EXPECTED_TABLES = {
    "agents",
    "agent_tokens",
    "alembic_version",
    "apps",
    "audit_log",
    "devices",
    "manifests",
    "plugins",
    "settings",
    "user_locks",
    "users",
}


@pytest.fixture
def migrated_db(tmp_path: Path) -> Iterator[str]:
    """Apply alembic upgrade head against a fresh temp SQLite file."""
    db = tmp_path / "test.sqlite"
    api_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")
    yield f"sqlite:///{db}"


def test_all_kernel_tables_exist(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == EXPECTED_TABLES


def test_settings_row_seeded_with_defaults(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    with engine.connect() as conn:
        rows = conn.execute(text("SELECT * FROM settings")).all()
    assert len(rows) == 1
    row = dict(rows[0]._mapping)
    assert row == {
        "id": 1,
        "agent_tick_seconds": 60,
        "manifest_tick_seconds": 3600,
        "plugin_resync_seconds": 300,
        "plugin_reconcile_timeout_seconds": 30,
        "audit_retention_days": 90,
    }


def test_settings_singleton_check(migrated_db: str) -> None:
    """Inserting a second settings row violates the CHECK(id = 1) constraint."""
    engine = create_engine(migrated_db)
    with engine.connect() as conn:
        with pytest.raises(Exception, match="CHECK constraint"):
            conn.execute(
                text(
                    "INSERT INTO settings (id, agent_tick_seconds, manifest_tick_seconds,"
                    " plugin_resync_seconds, plugin_reconcile_timeout_seconds,"
                    " audit_retention_days) VALUES (2, 60, 3600, 300, 30, 90)"
                )
            )


def test_devices_owner_fk_to_users(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    inspector = inspect(engine)
    fks = inspector.get_foreign_keys("devices")
    fk_to_users = [fk for fk in fks if fk["referred_table"] == "users"]
    assert len(fk_to_users) == 1
    assert fk_to_users[0]["constrained_columns"] == ["owner_id"]
    assert fk_to_users[0]["referred_columns"] == ["id"]


def test_users_username_unique_index(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    inspector = inspect(engine)
    indexes = {idx["name"]: idx for idx in inspector.get_indexes("users")}
    assert "ix_users_username" in indexes
    assert indexes["ix_users_username"]["column_names"] == ["username"]
    assert indexes["ix_users_username"]["unique"]


def test_devices_slug_unique_index(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    inspector = inspect(engine)
    indexes = {idx["name"]: idx for idx in inspector.get_indexes("devices")}
    assert "ix_devices_slug" in indexes
    assert indexes["ix_devices_slug"]["unique"]


def test_plugins_composite_pk(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    inspector = inspect(engine)
    pk = inspector.get_pk_constraint("plugins")
    assert pk["constrained_columns"] == ["type", "instance_id"]


def test_agent_tokens_unique_token_hash_index(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    inspector = inspect(engine)
    uqs = inspector.get_unique_constraints("agent_tokens")
    assert any(uq["column_names"] == ["token_hash"] for uq in uqs)


def test_audit_log_indexes_present(migrated_db: str) -> None:
    engine = create_engine(migrated_db)
    inspector = inspect(engine)
    indexes = {idx["name"]: idx["column_names"] for idx in inspector.get_indexes("audit_log")}
    assert "ix_audit_log_occurred_at" in indexes
    assert indexes["ix_audit_log_occurred_at"] == ["occurred_at"]
    assert "ix_audit_log_target_kind_target_id" in indexes
    assert indexes["ix_audit_log_target_kind_target_id"] == ["target_kind", "target_id"]


def test_agent_tokens_id_is_integer_pk(migrated_db: str) -> None:
    """Surrogate INTEGER id (was UUID before; flattened in this PR)."""
    engine = create_engine(migrated_db)
    inspector = inspect(engine)
    cols = {c["name"]: c for c in inspector.get_columns("agent_tokens")}
    assert cols["id"]["type"].__class__.__name__ == "INTEGER"


def test_downgrade_drops_all_tables(tmp_path: Path) -> None:
    """upgrade head → downgrade base leaves only alembic_version."""
    db = tmp_path / "test.sqlite"
    api_dir = Path(__file__).resolve().parents[1]
    cfg = Config(str(api_dir / "alembic.ini"))
    cfg.set_main_option("script_location", str(api_dir / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{db}")
    command.upgrade(cfg, "head")
    command.downgrade(cfg, "base")

    engine = create_engine(f"sqlite:///{db}")
    inspector = inspect(engine)
    assert set(inspector.get_table_names()) == {"alembic_version"}
