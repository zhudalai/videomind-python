"""Alembic environment configuration."""

from __future__ import annotations

import sys
from logging.config import fileConfig
from pathlib import Path

from alembic import context
from sqlalchemy import pool, create_engine
from sqlalchemy import create_engine

# Ensure src directory is in path
sys.path.insert(0, str(Path(__file__).parent.parent / "src"))

from videomind.config import get_settings
from videomind.infrastructure.storage.models import Base

# ───────────────────────────── Alembic Config ─────────────────────────────

config = context.config

# Interpret the config file for logging
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# Target metadata for autogenerate
target_metadata = Base.metadata

# ───────────────────────────── Get Database URL from Config ─────────────────────────────


def get_database_url() -> str:
    """Read async database URL from settings and convert to sync."""
    settings = get_settings()
    # alembic needs sync URL, convert postgresql+asyncpg:// to postgresql://
    url = settings.database_url.replace("+asyncpg", "")
    return url


# ───────────────────────────── Offline Migrations ─────────────────────────────


def run_migrations_offline() -> None:
    """Offline mode: generate SQL script without connecting to DB."""
    url = get_database_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        compare_server_default=True,
        include_schemas=False,
    )

    with context.begin_transaction():
        context.run_migrations()


# ───────────────────────────── Online Migrations ─────────────────────────────


def run_migrations_online() -> None:
    """Online mode: connect to DB and run migrations."""
    url = get_database_url()

    # Create sync engine (alembic needs sync connection)
    sync_url = url.replace("+asyncpg", "")
    connectable = create_engine(sync_url, poolclass=pool.NullPool)

    with connectable.connect() as connection:
        context.configure(
            connection=connection,
            target_metadata=target_metadata,
            compare_type=True,
            compare_server_default=True,
            include_schemas=False,
        )

        with context.begin_transaction():
            context.run_migrations()


# ───────────────────────────── Entry Point ─────────────────────────────

if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()