# app.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties

from bot.config import settings
from bot.db_repo.base import engine
from bot.db_repo.models import Base
from bot.handlers.history_inline import history_router

from bot.handlers.main_menu import main_menu_router
from bot.handlers.help_inline import help_router
from bot.handlers.plants_inline import plants_router
from bot.handlers.calendar_inline import calendar_router
from bot.handlers.schedule_delete_inline import delete_router
from bot.handlers.schedule_inline import router as schedule_router
from bot.handlers.quick_done_inline import router as quick_done_router


from bot.scheduler import start_scheduler, plan_all_active


async def init_db_if_needed():
    use_alembic = getattr(settings, "USE_ALEMBIC", True)
    if not use_alembic:
        async with engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)


async def main():
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    logging.getLogger("aiogram.event").setLevel(logging.WARNING)

    await init_db_if_needed()

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
    )
    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(main_menu_router)
    dp.include_router(history_router)
    dp.include_router(help_router)
    dp.include_router(plants_router)
    dp.include_router(calendar_router)
    dp.include_router(schedule_router)
    dp.include_router(quick_done_router)
    dp.include_router(delete_router)

    start_scheduler()
    await plan_all_active()

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())