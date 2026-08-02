"""Async SQLAlchemy engine/session management."""

import os
from collections.abc import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.orm import DeclarativeBase

from .config import get_settings


class Base(DeclarativeBase):
    pass


_engine = None
_session_factory: async_sessionmaker[AsyncSession] | None = None


def get_engine():
    global _engine, _session_factory
    if _engine is None:
        settings = get_settings()
        url = settings.database_url
        kwargs: dict = {"pool_pre_ping": True}
        if url.startswith("sqlite"):
            # Ensure the parent directory for the SQLite file exists.
            path = url.split("///")[-1]
            if path and path != ":memory:":
                os.makedirs(os.path.dirname(os.path.abspath(path)) or ".", exist_ok=True)
        else:
            # Cap connections: worker runs max_jobs stories x ~2 short sessions each,
            # plus API request sessions, against Postgres max_connections.
            kwargs.update(pool_size=settings.db_pool_size, max_overflow=settings.db_max_overflow)
        _engine = create_async_engine(url, **kwargs)
        if url.startswith("sqlite"):
            # SQLite ignores foreign keys unless asked. Without this, dev and tests
            # silently skip ON DELETE rules that Postgres enforces in production,
            # so cascade and SET NULL bugs stay invisible until launch.
            from sqlalchemy import event

            @event.listens_for(_engine.sync_engine, "connect")
            def _enable_sqlite_foreign_keys(dbapi_connection, _record):
                cursor = dbapi_connection.cursor()
                cursor.execute("PRAGMA foreign_keys=ON")
                cursor.close()

        _session_factory = async_sessionmaker(_engine, expire_on_commit=False)
    return _engine


def get_session_factory() -> async_sessionmaker[AsyncSession]:
    get_engine()
    assert _session_factory is not None
    return _session_factory


async def get_db() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding a request-scoped session."""
    async with get_session_factory()() as session:
        yield session


async def init_db() -> None:
    """Ensure the schema exists.

    Postgres schema is owned by Alembic (the compose `migrate` service runs
    `alembic upgrade head` before api/worker start), so this only creates tables
    for SQLite, which is used by tests and keyless local development. The advisory
    lock stays as a safety net for anyone running Postgres without the migrate step.
    """
    from sqlalchemy import text

    from . import models  # noqa: F401  (register mappings)

    engine = get_engine()
    async with engine.begin() as conn:
        if conn.dialect.name == "postgresql":
            await conn.execute(text("SELECT pg_advisory_xact_lock(823471)"))
            has_schema = (
                await conn.execute(text("SELECT to_regclass('public.alembic_version')"))
            ).scalar() is not None
            if has_schema:
                return  # Alembic is in charge; do not create anything behind its back
        await conn.run_sync(Base.metadata.create_all)


async def dispose_engine() -> None:
    global _engine, _session_factory
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        _session_factory = None
