# db_repo/base.py
import os
from contextlib import asynccontextmanager
from typing import Any, Iterable

from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

# ---------- Declarative Base для моделей ----------
class Base(DeclarativeBase):
    pass

# ---------- Engine + фабрика сессий ----------
DATABASE_URL = os.getenv("DATABASE_URL", "postgresql+asyncpg://bot:bot@db:5432/watering")

engine = create_async_engine(
    DATABASE_URL,
    echo=False,
    pool_pre_ping=True,
    future=True,
)

AsyncSessionLocal = async_sessionmaker(
    engine,
    expire_on_commit=False,
    class_=AsyncSession,
)

# ---------- Базовый репозиторий ----------
class BaseRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def add(self, obj: Any) -> Any:
        self.session.add(obj)
        await self.session.flush()
        return obj

    async def add_all(self, objs: Iterable[Any]) -> None:
        self.session.add_all(list(objs))
        await self.session.flush()

# ---------- Удобный контекст сессии ----------
@asynccontextmanager
async def session_scope() -> AsyncSession:
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise