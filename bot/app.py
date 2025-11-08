# app.py
import asyncio
import logging
from aiogram import Bot, Dispatcher
from aiogram.client.session.aiohttp import AiohttpSession
from aiogram.enums import ParseMode
from aiogram.fsm.storage.memory import MemoryStorage
from aiogram.client.default import DefaultBotProperties
from aiohttp import ClientTimeout, TCPConnector
from aiohttp_socks import ProxyConnector

from bot.config import settings
from bot.db_repo.base import engine
from bot.db_repo.models import Base
from bot.handlers.history_inline import history_router
from bot.handlers.main_menu import main_menu_router
from bot.handlers.help_inline import help_router
from bot.handlers.timezone import timezone_router
from bot.handlers.plants_inline import plants_router
from bot.handlers.calendar_inline import calendar_router
from bot.handlers.schedule_delete_inline import delete_router
from bot.handlers.schedule_inline import router as schedule_router
from bot.handlers.quick_done_inline import router as quick_done_router
from bot.handlers.remind_actions import router as reminder_router
from bot.handlers.start import router as start_router
from bot.handlers.settings_inline import settings_router as settings_menu_router
from bot.handlers.settings_share_wizard import settings_router as settings_share_router
from bot.handlers.settings_subscriptions import settings_router as settings_subs_router
from bot.handlers.share_codes_inline import codes_router as codes_router
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

    proxy_url = getattr(settings, "PROXY_URL", None)
    timeout = ClientTimeout(total=60)

    session = None
    if proxy_url:
        connector = ProxyConnector.from_url(proxy_url)
        session = AiohttpSession(connector=connector, timeout=timeout)
        logging.info(f"[BOT] Proxy enabled: {proxy_url}")
    else:
        logging.info("[BOT] Proxy not set, using direct connection")

    bot = Bot(
        token=settings.BOT_TOKEN,
        default=DefaultBotProperties(parse_mode=ParseMode.HTML),
        session=session,
    )

    dp = Dispatcher(storage=MemoryStorage())

    dp.include_router(start_router)
    dp.include_router(main_menu_router)

    dp.include_router(help_router)
    dp.include_router(history_router)
    dp.include_router(plants_router)

    dp.include_router(calendar_router)

    dp.include_router(schedule_router)
    dp.include_router(reminder_router)
    dp.include_router(quick_done_router)
    dp.include_router(delete_router)


    dp.include_router(settings_menu_router)
    dp.include_router(settings_share_router)
    dp.include_router(settings_subs_router)
    dp.include_router(timezone_router)
    dp.include_router(codes_router)

    start_scheduler()
    await plan_all_active()

    try:
        await dp.start_polling(bot)
    finally:
        await bot.session.close()
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())