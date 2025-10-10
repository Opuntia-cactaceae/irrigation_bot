from __future__ import annotations
from logging.config import fileConfig
from sqlalchemy import pool
from alembic import context
from pathlib import Path
import sys
ROOT = Path(__file__).resolve().parents[3]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))
# 1) Импортируем Base и настройки
from bot.db_repo.models import Base
from bot.config import settings

# Alembic Config
config = context.config
if config.config_file_name is not None:
    fileConfig(config.config_file_name)

# 2) Метаданные для автогенерации
target_metadata = Base.metadata

# --- ASYNC SETUP ---
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

def get_url() -> str:
    return settings.DATABASE_URL  # например, "postgresql+asyncpg://..."

def run_migrations_offline():
    url = get_url()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        compare_type=True,         # полезно сравнивать типы колонок
    )
    with context.begin_transaction():
        context.run_migrations()

def do_run_migrations(connection):
    context.configure(
        connection=connection,
        target_metadata=target_metadata,
        compare_type=True,
    )
    with context.begin_transaction():
        context.run_migrations()

async def run_migrations_online():
    connectable: AsyncEngine = create_async_engine(get_url(), poolclass=pool.NullPool)
    async with connectable.connect() as connection:
        await connection.run_sync(do_run_migrations)
    await connectable.dispose()

# Entrypoints (Alembic вызывает их сам)
if context.is_offline_mode():
    run_migrations_offline()
else:
    import asyncio
    asyncio.run(run_migrations_online())