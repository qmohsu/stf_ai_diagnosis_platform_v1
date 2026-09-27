"""SQLAlchemy 2.0 declarative base and async engine/session factory.

Author: Xiangzhu Yan
"""

import os
from typing import AsyncIterator

from sqlalchemy import MetaData
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase

from stf_v3.settings import settings

# Deterministic constraint names so Alembic migrations are reviewable and
# downgrades can drop constraints by name.
_NAMING_CONVENTION = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


class Base(DeclarativeBase):
    """Declarative base shared by every V3 model."""

    metadata = MetaData(naming_convention=_NAMING_CONVENTION)


# PROD-15A (FM-45): the runtime role has a connection limit (ops_roles.sql),
# sized from every pool's maximum: this engine 5 + 5 overflow + the queue
# connector 4 = 14 per process (API, container worker) and + 1 for the GPU
# worker's ingest engine = 15 -> 43, limit 50 (7 spare for ops scripts run in
# a container).  Read from the environment: settings.py is gate-managed.
DB_MAX_OVERFLOW = int(os.environ.get("STF_V3_DB_MAX_OVERFLOW", "5"))


def create_engine() -> AsyncEngine:
    """Creates the async engine from settings.

    Returns:
        A configured ``AsyncEngine`` (psycopg 3 async driver).
    """
    return create_async_engine(
        settings.database_url,
        pool_size=settings.db_pool_size,
        max_overflow=DB_MAX_OVERFLOW,
        pool_pre_ping=True,
    )


engine: AsyncEngine = create_engine()
SessionLocal = async_sessionmaker(engine, expire_on_commit=False)


async def get_session() -> AsyncIterator[AsyncSession]:
    """FastAPI dependency yielding one ``AsyncSession`` per request.

    Yields:
        An ``AsyncSession`` bound to the module engine.
    """
    async with SessionLocal() as session:
        yield session
