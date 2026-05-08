"""SQLAlchemy engine factory + FastAPI session dependency.

Sync SQLAlchemy throughout (per ADR/discussion: SQLite-WAL handles homelab-scale
concurrency fine, async adds complexity Alembic can't share). Engine is process-
wide and cached; sessions are per-request.

Connection-event hooks enable two SQLite features that production setups want:

- ``PRAGMA foreign_keys = ON`` — SQLite ignores FK constraints by default.
- ``PRAGMA journal_mode = WAL`` — concurrent readers + a writer without
  blocking, durable.
"""

from __future__ import annotations

from collections.abc import Iterator
from functools import lru_cache
from typing import Any

from sqlalchemy import Engine, create_engine, event
from sqlmodel import Session

from curfew.config import Settings, get_config


@event.listens_for(Engine, "connect")
def _sqlite_connection_pragmas(dbapi_connection: Any, _connection_record: Any) -> None:
    """Apply per-connection SQLite pragmas when an engine opens a connection."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA foreign_keys = ON")
    cursor.execute("PRAGMA journal_mode = WAL")
    cursor.close()


def make_engine(config: Settings) -> Engine:
    """Build a SQLAlchemy engine from the given Settings."""
    return create_engine(f"sqlite:///{config.db_path}", echo=False, future=True)


@lru_cache(maxsize=1)
def get_engine() -> Engine:
    """Return the process-wide engine, cached after first call."""
    return make_engine(get_config())


def reset_engine_cache() -> None:
    """Clear the cached engine; useful in tests."""
    get_engine.cache_clear()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a per-request SQLModel ``Session``."""
    with Session(get_engine()) as session:
        yield session
