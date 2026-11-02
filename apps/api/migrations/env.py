"""Alembic environment for the async Winnie database.

The URL comes from the same YAML settings as the application, so migrations
cannot silently target a different database. Set ``WINNIE_CONFIG`` to use a
non-default config file.
"""

from __future__ import annotations

import asyncio
import os
from logging.config import fileConfig
from pathlib import Path

from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context
from winnie_core.auth import models as _auth_models  # noqa: F401
from winnie_core.db import Base
from winnie_mail import models as _mail_models  # noqa: F401
from winnie_meetings import models as _meeting_models  # noqa: F401
from winnie_phone import models as _phone_models  # noqa: F401
from winnie_proactive import models as _proactive_models  # noqa: F401
from winnie_push import models as _push_models  # noqa: F401
from winnie_api.settings import load_settings

config = context.config
if config.config_file_name is not None:
    # fileConfig defaults to disable_existing_loggers=True, which flips
    # ``disabled`` on every logger created before this line and never restores
    # it. Run in-process — a test, a script, an embedder — and the whole
    # application's logging goes silent for the rest of the process, with no
    # error to show for it: ``logger.warning(...)`` simply stops emitting.
    fileConfig(config.config_file_name, disable_existing_loggers=False)

_api_root = Path(__file__).resolve().parents[1]
config_path = Path(os.environ.get("WINNIE_CONFIG", _api_root / "config.yaml"))
settings = load_settings(config_path)
url = settings.resolve_database_url()
config.set_main_option("sqlalchemy.url", url.replace("%", "%%"))
target_metadata = Base.metadata


def run_migrations_offline() -> None:
    """Emit SQL without connecting to the database."""
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection: Connection) -> None:
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
        render_as_batch=True,
    )
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    connectable = async_engine_from_config(
        config.get_section(config.config_ini_section, {}),
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()


def run_migrations_online() -> None:
    asyncio.run(run_async_migrations())


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
