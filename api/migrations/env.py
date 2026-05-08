"""Alembic env for curfew-api.

Imports SQLModel.metadata from the core package so the migration sees every
table the kernel declares. Honours CURFEW_DB_PATH if set; otherwise falls back
to the sqlalchemy.url in alembic.ini (sqlite:///./state.sqlite).
"""

from __future__ import annotations

import os
from logging.config import fileConfig

from alembic import context
from curfew.models import SQLModel
from sqlalchemy import engine_from_config, pool

config = context.config

# Allow operators to override the DB location via env (matches CURFEW_DB_PATH
# documented in PLAN.md §"Configuration and settings").
if db_path := os.getenv("CURFEW_DB_PATH"):
    config.set_main_option("sqlalchemy.url", f"sqlite:///{db_path}")

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = SQLModel.metadata


def run_migrations_offline() -> None:
    """Run migrations in 'offline' mode (emits SQL without connecting)."""
    url = config.get_main_option("sqlalchemy.url")
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    """Run migrations in 'online' mode against a live connection."""
    section = config.get_section(config.config_ini_section, {})
    connectable = engine_from_config(
        section,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            render_as_batch=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
