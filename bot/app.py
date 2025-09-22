# app.py
import asyncio
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage

from bot.config import settings

# БД
from bot.db_repo.base import engine
from bot.db_repo.models import Base

# Роутеры
from bot.handlers.main_menu import main_menu_router
from bot.handlers.plants_inline import plants_router
from bot.handlers.calendar_inline import calendar_router
from bot.handlers.schedule_inline import router as schedule_router
from bot.handlers.quick_done_inline import router as quick_done_router
# (если будет экран настроек)
# from bot.handlers.settings_inline import settings_router

# Планировщик
from bot.scheduler import start_scheduler, plan_all_active


async def main():
    # 1) Инициализация БД (если без Alembic)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    # 2) Бот + диспетчер с FSM
    bot = Bot(token=settings.BOT_TOKEN, parse_mode=ParseMode.HTML)
    dp = Dispatcher(storage=MemoryStorage())

    # 3) Роутеры
    dp.include_router(main_menu_router)
    dp.include_router(plants_router)
    dp.include_router(calendar_router)
    dp.include_router(schedule_router)
    dp.include_router(quick_done_router)
    # dp.include_router(settings_router)

    # 4) Планировщик: старт + перепланировать все активные
    start_scheduler()
    await plan_all_active(bot)

    # 5) Старт поллинга
    await dp.start_polling(bot)


if __name__ == "__main__":
    asyncio.run(main())