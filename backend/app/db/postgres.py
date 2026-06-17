"""
Async PostgreSQL engine and session factory for SpAIder.

Usage
-----
FastAPI dependency injection (preferred)::

    from app.db.postgres import get_db_session
    from sqlalchemy.ext.asyncio import AsyncSession

    @router.get("/example")
    async def handler(db: AsyncSession = Depends(get_db_session)):
        result = await db.execute(select(ConnectorConfig))
        ...

Direct use (scripts, migrations)::

    from app.db.postgres import async_session_factory
    async with async_session_factory() as session:
        async with session.begin():
            session.add(...)

Schema bootstrap
----------------
Call ``init_db()`` once at application startup (e.g. in ``app.main`` lifespan)
to run ``CREATE TABLE IF NOT EXISTS`` for all mapped models::

    from app.db.postgres import init_db
    await init_db()

Connection pool tuning
----------------------
``pool_size`` and ``max_overflow`` are sized for a single-process API server.
Increase ``pool_size`` or switch to ``NullPool`` if the service is deployed
with multiple Uvicorn workers (each worker gets its own pool, so multiply
accordingly to stay within Postgres ``max_connections``).
"""
from __future__ import annotations

import logging
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from app.config import settings

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Declarative base — all ORM models inherit from this
# ---------------------------------------------------------------------------


class Base(DeclarativeBase):
    """
    Shared SQLAlchemy 2.0 declarative base.

    Import this in every model module::

        from app.db.postgres import Base

        class MyModel(Base):
            __tablename__ = "my_table"
            ...
    """


# ---------------------------------------------------------------------------
# Engine
# ---------------------------------------------------------------------------
#
# ``pool_pre_ping=True``  — issue a cheap SELECT 1 before handing out a
#     connection; silently replaces stale connections after DB restarts or
#     load-balancer idle-timeout drops.
# ``pool_size=10``        — up to 10 persistent connections per process.
# ``max_overflow=20``     — allow up to 20 additional connections under burst
#     load; they are closed when the burst subsides.
# ``echo=False``          — set to True (or use SQLALCHEMY_ECHO=1 env var)
#     only in development; logs every SQL statement to stdout.
# ---------------------------------------------------------------------------

_engine: AsyncEngine | None = None


def get_engine() -> AsyncEngine:
    """
    Return the module-level async engine, creating it on first call.

    The engine is a process-singleton: one pool shared across all coroutines
    in the process.  Calling this multiple times is safe — the same object is
    returned after the first call.
    """
    global _engine
    if _engine is None:
        _engine = create_async_engine(
            settings.postgres_url,
            pool_pre_ping=True,
            pool_size=10,
            max_overflow=20,
            echo=settings.debug,
        )
        logger.info(
            "PostgreSQL async engine created (url=%s)",
            # Redact password in logs
            settings.postgres_url.split("@")[-1] if "@" in settings.postgres_url else settings.postgres_url,
        )
    return _engine


# ---------------------------------------------------------------------------
# Session factory
# ---------------------------------------------------------------------------
#
# ``expire_on_commit=False`` — ORM objects remain accessible after
#     ``session.commit()`` without issuing a lazy SELECT.  Critical in async
#     contexts where a second await inside the same request would trigger an
#     implicit IO on an expired attribute.
# ``autoflush=False``       — prevents unexpected flushes mid-transaction;
#     the caller controls exactly when writes hit the DB.
# ---------------------------------------------------------------------------

async_session_factory: async_sessionmaker[AsyncSession] = async_sessionmaker(
    bind=get_engine(),
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


# ---------------------------------------------------------------------------
# FastAPI dependency
# ---------------------------------------------------------------------------


async def get_db_session() -> AsyncIterator[AsyncSession]:
    """
    FastAPI dependency that yields a transactional ``AsyncSession``.

    Each HTTP request gets its own session.  The transaction is committed on
    clean exit and rolled back on any exception, then the session is closed.

    Example::

        @router.post("/connectors")
        async def create(db: AsyncSession = Depends(get_db_session)):
            db.add(ConnectorConfig(...))
            # commit happens automatically on function return
    """
    async with async_session_factory() as session:
        async with session.begin():
            try:
                yield session
            except Exception:
                await session.rollback()
                raise


# ---------------------------------------------------------------------------
# Schema bootstrap
# ---------------------------------------------------------------------------


async def init_db() -> None:
    """
    Create all tables defined under ``Base.metadata`` if they do not exist.

    Call once during application startup::

        from app.db.postgres import init_db
        @asynccontextmanager
        async def lifespan(app):
            await init_db()
            yield

    This is intentionally a lightweight ``CREATE TABLE IF NOT EXISTS`` — it is
    NOT a migration system.  Use Alembic for schema evolution in production.
    """
    engine = get_engine()
    async with engine.begin() as conn:
        # Import all model modules here so their tables are registered on
        # Base.metadata before create_all runs.
        import app.models.connector  # noqa: F401  — registers ORM tables

        await conn.run_sync(Base.metadata.create_all)
        logger.info("PostgreSQL schema bootstrap complete.")


async def dispose_engine() -> None:
    """
    Gracefully drain and close all pooled connections.

    Call during application shutdown so Postgres does not log unexpected
    client disconnects::

        @asynccontextmanager
        async def lifespan(app):
            await init_db()
            yield
            await dispose_engine()
    """
    global _engine
    if _engine is not None:
        await _engine.dispose()
        _engine = None
        logger.info("PostgreSQL async engine disposed.")
