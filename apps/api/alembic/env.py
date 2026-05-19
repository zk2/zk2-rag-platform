"""Alembic env using sync psycopg driver (Alembic doesn't run async natively)."""

from __future__ import annotations

from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

from zk2.config import get_settings
from zk2.core.db import Base

# Import models so they're registered on Base.metadata
import zk2.auth.models  # noqa: F401
import zk2.bots.models  # noqa: F401
import zk2.llm.models  # noqa: F401
import zk2.sources.models  # noqa: F401

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

settings = get_settings()
config.set_main_option("sqlalchemy.url", settings.db.sync_dsn)

target_metadata = Base.metadata


def run_migrations_offline() -> None:
    context.configure(
        url=settings.db.sync_dsn,
        target_metadata=target_metadata,
        literal_binds=True,
        compare_type=True,
        compare_server_default=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    cfg = config.get_section(config.config_ini_section, {})
    cfg["sqlalchemy.url"] = settings.db.sync_dsn
    connectable = engine_from_config(
        cfg,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
        )
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
