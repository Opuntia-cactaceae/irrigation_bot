# bot/scheduler.py
from __future__ import annotations
from datetime import datetime
import os
import pytz

from aiogram import Bot
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore

from bot.db_repo.base import AsyncSessionLocal
from bot.db_repo.models import Schedule, ActionType, Event, Plant, User
from bot.db_repo.unit_of_work import new_uow
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.services.rules import next_by_interval, next_by_weekly

# --- JobStore: НУЖЕН синхронный URL (psycopg), не asyncpg ---
SYNC_DB_URL = (
    os.getenv("DATABASE_URL_SYNC")
    or os.getenv("DATABASE_URL", "postgresql+asyncpg://bot:bot@db:5432/watering").replace("+asyncpg", "")
)

jobstores = {
    "default": SQLAlchemyJobStore(url=SYNC_DB_URL, tablename="apscheduler_jobs"),
}
scheduler = AsyncIOScheduler(jobstores=jobstores)


# ==========================
# ВСПОМОГАТЕЛЬНОЕ
# ==========================
ACTION_EMOJI = {
    ActionType.WATERING: "💧",
    ActionType.FERTILIZING: "💊",
    ActionType.REPOTTING: "🪴",
}

def _job_id(schedule_id: int) -> str:
    return f"sch:{schedule_id}"  # уникально на расписание


# ==========================
# ЗАДАНИЕ: ОТПРАВИТЬ НАПОМИНАНИЕ
# ==========================
async def send_reminder(bot: Bot, schedule_id: int):
    """
    Вызывается APScheduler-ом в нужный момент.
    Отправляет сообщение и пишет auto Event (не сдвигает вручную график — следующий рассчитаем отдельно).
    """
    # Подтянем контекст по schedule_id
    async with AsyncSessionLocal() as session:
        sch: Schedule | None = await session.get(
            Schedule,
            schedule_id,
            options=(
                selectinload(Schedule.plant)
                .selectinload(Plant.user),
            ),
        )
        if not sch or not sch.active:
            return  # расписание выключено или удалено

        user: User = sch.plant.user
        plant: Plant = sch.plant

        # 1) отправка
        emoji = ACTION_EMOJI.get(sch.action, "•")
        action_text = {
            ActionType.WATERING: "Время полива",
            ActionType.FERTILIZING: "Время удобрить",
            ActionType.REPOTTING: "Время пересадки",
        }[sch.action]
        await bot.send_message(user.tg_user_id, f"{emoji} {action_text}: {plant.name}")

        # 2) лог авто-события
        ev = Event(plant_id=plant.id, action=sch.action, source="auto")
        session.add(ev)
        await session.commit()

    # 3) после отправки — перепланировать следующее наступление
    await plan_next_for_schedule(bot, schedule_id)


# ==========================
# РАСЧЁТ СЛЕДУЮЩЕГО ВРЕМЕНИ
# ==========================
def _calc_next_run_utc(*, sch: Schedule, user_tz: str, last_event_utc: datetime | None, now_utc: datetime) -> datetime:
    """
    Возвращает ближайшее наступление (UTC) для данного расписания, не раньше now_utc.
    """
    if sch.type == "interval":
        return next_by_interval(last_event_utc, sch.interval_days, sch.local_time, user_tz, now_utc)
    else:
        return next_by_weekly(last_event_utc, sch.weekly_mask, sch.local_time, user_tz, now_utc)


# ==========================
# ПЛАНИРОВАНИЕ ОДНОГО РАСПИСАНИЯ
# ==========================
async def plan_next_for_schedule(bot: Bot, schedule_id: int):
    """
    Пересчитать и (пере)создать job для конкретного Schedule.
    """
    async with AsyncSessionLocal() as session:
        sch: Schedule | None = await session.get(
            Schedule,
            schedule_id,
            options=(
                selectinload(Schedule.plant)
                .selectinload(Plant.user),
                selectinload(Schedule.plant).selectinload(Plant.events),
            ),
        )
        if not sch or not sch.active:
            # удалить джоб, если он остался
            try:
                scheduler.remove_job(_job_id(schedule_id))
            except Exception:
                pass
            return

        user = sch.plant.user
        tz = user.tz
        now_utc = datetime.now(tz=pytz.UTC)

        # последнее событие именно по этому действию (для данного растения)
        last = max(
            (e.done_at_utc for e in (sch.plant.events or []) if e.action == sch.action),
            default=None
        )

        run_at = _calc_next_run_utc(sch=sch, user_tz=tz, last_event_utc=last, now_utc=now_utc)

    # пересоздаём job
    job_id = _job_id(schedule_id)
    try:
        scheduler.remove_job(job_id)
    except Exception:
        pass

    scheduler.add_job(
        send_reminder,
        trigger="date",
        id=job_id,
        run_date=run_at,
        args=[bot, schedule_id],
        replace_existing=True,
        misfire_grace_time=3600,   # 1 час на отставание
        coalesce=True,
        max_instances=1,
    )


# ==========================
# ПЛАНИРОВАНИЕ ДЛЯ ВСЕХ АКТИВНЫХ
# ==========================
async def plan_all_active(bot: Bot):
    """
    Пройтись по всем активным расписаниям и перепланировать их job.
    Вызывай при старте бота.
    """
    async with AsyncSessionLocal() as session:
        q = (
            select(Schedule)
            .where(Schedule.active.is_(True))
            .options(
                selectinload(Schedule.plant).selectinload(Plant.user),
                selectinload(Schedule.plant).selectinload(Plant.events),
            )
        )
        schedules = (await session.execute(q)).scalars().all()

    for sch in schedules:
        await plan_next_for_schedule(bot, sch.id)


# ==========================
# ЖИЗНЕННЫЙ ЦИКЛ ПЛАНИРОВЩИКА
# ==========================
def start_scheduler():
    """
    Запуск APScheduler (один раз при старте приложения).
    """
    if not scheduler.running:
        scheduler.start()