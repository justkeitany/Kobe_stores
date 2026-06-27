from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase
from app.config import settings

engine = create_async_engine(
    settings.DATABASE_URL,
    pool_size=20,
    max_overflow=40,
    pool_pre_ping=True,
    pool_recycle=3600,
    echo=False,
)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
        except Exception:
            await session.rollback()
            raise
        finally:
            await session.close()


# Lightweight, idempotent column additions for upgrades on an existing DB.
# create_all() creates new *tables* but never alters existing ones, so columns
# added to a model after first install must be backfilled here. Postgres
# supports "ADD COLUMN IF NOT EXISTS", so each statement is safe to re-run.
_COLUMN_MIGRATIONS = (
    "ALTER TABLE streams ADD COLUMN IF NOT EXISTS delivery_mode VARCHAR(20) NOT NULL DEFAULT 'restream'",
    "ALTER TABLE streams ADD COLUMN IF NOT EXISTS quality VARCHAR(10) NOT NULL DEFAULT 'auto'",
    "ALTER TABLE streams ADD COLUMN IF NOT EXISTS proxy_country VARCHAR(8)",
    "ALTER TABLE streams ADD COLUMN IF NOT EXISTS force_adaptive BOOLEAN NOT NULL DEFAULT false",
    "ALTER TABLE playlists ADD COLUMN IF NOT EXISTS health VARCHAR(255)",
    "ALTER TABLE playlists ADD COLUMN IF NOT EXISTS channels JSON",
)


async def init_db():
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
        for stmt in _COLUMN_MIGRATIONS:
            await conn.execute(text(stmt))
