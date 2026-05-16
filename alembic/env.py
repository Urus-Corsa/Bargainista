import asyncio
import os
from logging.config import fileConfig

from sqlalchemy import pool
from sqlalchemy.ext.asyncio import async_engine_from_config

from alembic import context

# Import ORM metadata so autogenerate can compare models against the live schema.
from app.models.db_models import Base

config = context.config

# Wire up Python's logging from the alembic.ini [loggers] section.
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = Base.metadata


def _get_url() -> str:
    """Read the database URL from the environment.

    The DATABASE_URL env var uses the asyncpg driver prefix
    (postgresql+asyncpg://...), which is what async_engine_from_config expects.
    Falls back to the alembic.ini placeholder only if the env var is absent,
    which will cause a connection error — that's intentional.
    """
    return os.environ.get("DATABASE_URL", config.get_main_option("sqlalchemy.url"))


def run_migrations_offline() -> None:
    """Run migrations without an active DB connection (generates SQL script only)."""
    url = _get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def do_run_migrations(connection):
    context.configure(connection=connection, target_metadata=target_metadata)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    """Create an async engine and run migrations through a sync connection wrapper.

    NullPool is required here for the same reason it is used in Celery tasks:
    asyncpg binds connections to the event loop they were created on. Using NullPool
    ensures each `alembic upgrade head` invocation gets a fresh connection that is
    closed immediately after the migration completes, with no pooled connections
    lingering across event-loop boundaries.
    """
    configuration = config.get_section(config.config_ini_section, {})
    configuration["sqlalchemy.url"] = _get_url()

    connectable = async_engine_from_config(
        configuration,
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
