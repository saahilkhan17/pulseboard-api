"""Database connection and session factory using SQLAlchemy 2.0 async."""

from typing import AsyncGenerator
from sqlalchemy.ext.asyncio import (
    AsyncEngine,
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool, StaticPool

from app.config import get_settings


class Base(DeclarativeBase):
    """Base class for all SQLAlchemy ORM models."""
    pass


def get_engine_args(database_url: str) -> dict:
    """Return appropriate engine arguments depending on DB dialect."""
    args: dict = {}
    if database_url.startswith("sqlite"):
        args["connect_args"] = {"check_same_thread": False}
        if ":memory:" in database_url:
            args["poolclass"] = StaticPool
        else:
            args["poolclass"] = NullPool
    return args


def create_engine_from_url(database_url: str, echo: bool = False) -> AsyncEngine:
    """Create an async SQLAlchemy engine."""
    # Normalize sqlite URLs for aiosqlite if needed
    if database_url.startswith("sqlite://") and not database_url.startswith("sqlite+aiosqlite://"):
        database_url = database_url.replace("sqlite://", "sqlite+aiosqlite://", 1)

    engine_args = get_engine_args(database_url)
    return create_async_engine(
        database_url,
        echo=echo,
        future=True,
        **engine_args,
    )


settings = get_settings()
engine: AsyncEngine = create_engine_from_url(settings.DATABASE_URL, echo=settings.DB_ECHO)
async_session_factory = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autocommit=False,
    autoflush=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI dependency for yielding database sessions."""
    async with async_session_factory() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


async def init_db(target_engine: AsyncEngine = None) -> None:
    """Initialize database tables for testing or startup."""
    active_engine = target_engine or engine
    async with active_engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
