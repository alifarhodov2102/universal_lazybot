import os

from dotenv import load_dotenv
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase


load_dotenv()


# Railway or other cloud database URL.
DB_URL = os.getenv(
    "DATABASE_URL",
    "sqlite+aiosqlite:///./bot_database.db",
)


# ============================================================
# Base class
# ============================================================

class Base(DeclarativeBase):
    pass


# ============================================================
# Engine
# ============================================================

# PostgreSQL benefits from an explicit connection pool.
# Keep SQLite fallback simple because pool_size/max_overflow are
# not appropriate for every SQLite async pool implementation.
engine_options = {
    "echo": False,
    "pool_pre_ping": True,
}

if not DB_URL.startswith("sqlite"):
    engine_options.update(
        {
            "pool_size": 10,
            "max_overflow": 20,
            "pool_recycle": 3600,
        }
    )


engine = create_async_engine(
    DB_URL,
    **engine_options,
)


# ============================================================
# Session factory
# ============================================================

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
)


# ============================================================
# Database initialization
# ============================================================

async def init_db() -> None:
    """
    Create only missing database tables/indexes.

    IMPORTANT:
    create_all() is additive here. It does NOT drop or recreate the
    existing users table, subscriptions, expiry dates, templates, etc.
    """

    # Make absolutely sure every SQLAlchemy model is registered in
    # Base.metadata before create_all() runs.
    from . import models  # noqa: F401

    async with engine.begin() as conn:
        await conn.run_sync(
            Base.metadata.create_all
        )
