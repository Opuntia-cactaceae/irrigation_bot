# bot/scheduler.py
from __future__ import annotations

import logging
import os
from datetime import datetime
import pytz

from aiogram import Bot
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.config import settings
from bot.db_repo.base import AsyncSessionLocal
from bot.db_repo.models import (
    ActionType,
    Event,
    Plant,
    Schedule,
    ScheduleType,
    User,
)
from bot.services.rules import next_by_interval, next_by_weekly

# ----------------------------------
# ЛОГГЕР
# ----------------------------------
logger = logging.getLogger(__name__)

# ----------------------------------
# APSCHEDULER: jobstore (SQLAlchemy)
# НУЖЕН синхронный драйвер (psycopg/pg8000), не asyncpg
# ----------------------------------
SYNC_DB_URL = (
    os.getenv("DATABASE_URL_SYNC")
    or os.getenv("DATABASE_URL", "postgresql+asyncpg://bot:bot@db:5432/watering").replace(
        "+asyncpg", ""
    )
)

jobstores = {"default": SQLAlchemyJobStore(url=SYNC_DB_URL, tablename="apscheduler_jobs")}
scheduler = AsyncIOScheduler(jobstores=jobstores)  # таймзону не меняем, как и просили

# ----------------------------------
# ВСПОМОГАТЕЛЬНОЕ
# ----------------------------------
ACTION_EMOJI = {
    ActionType.WATERING: "💧",
    ActionType.FERTILIZING: "💊",
    ActionType.REPOTTING: "🪴",
}


def _job_id(schedule_id: int) -> str:
    return f"sch:{schedule_id}"


def _is_interval_type(t) -> bool:
    """
    Унифицированная проверка типа расписания:
    поддерживаем и строки ("interval"/"weekly"), и Enum ScheduleType.
    """
    if t == ScheduleType.INTERVAL:
        return True
    if t == ScheduleType.WEEKLY:
        return False
    if isinstance(t, str):
        return t == "interval"
    if hasattr(t, "value"):
        return t.value == "interval"
    return False


def _calc_next_run_utc(
    *, sch: Schedule, user_tz: str, last_event_utc: datetime | None, now_utc: datetime
) -> datetime:
    """
    Возвращает ближайшее наступление (UTC) для данного расписания, строго > now_utc.
    """
    if _is_interval_type(sch.type):
        return next_by_interval(last_event_utc, sch.interval_days, sch.local_time, user_tz, now_utc)
    else:
        return next_by_weekly(last_event_utc, sch.weekly_mask, sch.local_time, user_tz, now_utc)


# ----------------------------------
# APSCHEDULER DIAGNOSTICS
# ----------------------------------
def _on_job_event(event: JobExecutionEvent):
    try:
        job = scheduler.get_job(event.job_id)
        next_run = getattr(job, "next_run_time", None)
        kind = (
            "EXECUTED"
            if event.code == EVENT_JOB_EXECUTED
            else "ERROR"
            if event.code == EVENT_JOB_ERROR
            else "MISSED"
        )
        logger.info(
            "[JOB EVT] type=%s job_id=%s next=%s store=%s",
            kind,
            event.job_id,
            next_run.isoformat() if next_run else None,
            getattr(job, "jobstore", None),
        )
        if event.code == EVENT_JOB_ERROR and event.exception:
            logger.exception("[JOB ERROR EXC] job_id=%s", event.job_id, exc_info=event.exception)
    except Exception:
        logger.exception("[JOB EVT] handler error")


def _heartbeat():
    # Пульс: видно, что планировщик жив и сколько задач сейчас зарегистрировано
    try:
        logger.info("[SCHED HEARTBEAT] jobs=%d", len(scheduler.get_jobs()))
    except Exception:
        logger.exception("[SCHED HEARTBEAT] failed")


# ----------------------------------
# ЗАДАНИЕ: ОТПРАВИТЬ НАПОМИНАНИЕ
# ----------------------------------
async def send_reminder(schedule_id: int):
    """
    Вызывается APScheduler-ом в нужный момент.
    ВНИМАНИЕ: никаких живых объектов (Bot/Session) в args! Bot создаём локально.
    """
    logger.info("[JOB START] schedule_id=%s", schedule_id)

    bot = Bot(token=settings.BOT_TOKEN)
    try:
        async with AsyncSessionLocal() as session:
            sch: Schedule | None = await session.get(
                Schedule,
                schedule_id,
                options=(selectinload(Schedule.plant).selectinload(Plant.user),),
            )
            if not sch or not sch.active:
                logger.warning("[JOB SKIP] schedule_id=%s inactive/missing", schedule_id)
                return

            user: User = sch.plant.user
            plant: Plant = sch.plant

            emoji = ACTION_EMOJI.get(sch.action, "•")
            action_text = {
                ActionType.WATERING: "Время полива",
                ActionType.FERTILIZING: "Время удобрить",
                ActionType.REPOTTING: "Время пересадки",
            }[sch.action]

            try:
                await bot.send_message(user.tg_user_id, f"{emoji} {action_text}: {plant.name}")
                logger.info(
                    "[SEND OK] user_id=%s plant_id=%s action=%s", user.id, plant.id, sch.action
                )
            except Exception as e:
                logger.exception("[SEND ERR] schedule_id=%s: %s", schedule_id, e)

            # лог авто-события
            ev = Event(plant_id=plant.id, action=sch.action, source="auto")
            session.add(ev)
            await session.commit()
            logger.debug("[EVENT LOGGED] event_id=%s schedule_id=%s", ev.id, schedule_id)
    finally:
        await bot.session.close()

    # перепланировать следующее наступление
    await plan_next_for_schedule(schedule_id)


# ----------------------------------
# ПЛАНИРОВАНИЕ ОДНОГО РАСПИСАНИЯ
# ----------------------------------
async def plan_next_for_schedule(schedule_id: int):
    """
    Пересчитать и (пере)создать job для конкретного Schedule.
    """
    async with AsyncSessionLocal() as session:
        sch: Schedule | None = await session.get(
            Schedule,
            schedule_id,
            options=(
                selectinload(Schedule.plant).selectinload(Plant.user),
                selectinload(Schedule.plant).selectinload(Plant.events),
            ),
        )
        if not sch or not sch.active:
            # удалить джоб, если он остался
            try:
                scheduler.remove_job(_job_id(schedule_id))
                logger.info("[JOB REMOVED] schedule_id=%s", schedule_id)
            except Exception:
                pass
            return

        user = sch.plant.user
        tz = user.tz
        now_utc = datetime.now(tz=pytz.UTC)

        # последнее событие именно по этому действию (для данного растения)
        last = max(
            (e.done_at_utc for e in (sch.plant.events or []) if e.action == sch.action),
            default=None,
        )

        run_at = _calc_next_run_utc(
            sch=sch, user_tz=tz, last_event_utc=last, now_utc=now_utc
        )
        try:
            # для наглядности — выведем и локальное время пользователя
            loc = pytz.timezone(tz)
            logger.info(
                "[PLAN] schedule_id=%s user_id=%s plant_id=%s action=%s run_at_utc=%s run_at_local=%s tz=%s",
                schedule_id,
                user.id,
                sch.plant.id,
                sch.action,
                run_at.isoformat(),
                run_at.astimezone(loc).strftime("%Y-%m-%d %H:%M:%S"),
                tz,
            )
        except Exception:
            logger.info(
                "[PLAN] schedule_id=%s user_id=%s plant_id=%s action=%s run_at_utc=%s tz=%s",
                schedule_id,
                user.id,
                sch.plant.id,
                sch.action,
                run_at.isoformat(),
                tz,
            )

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
        args=[schedule_id],  # только примитивы!
        jobstore="default",  # явно сохраняем в БД
        replace_existing=True,
        misfire_grace_time=3600,  # 1 час на отставание
        coalesce=True,
        max_instances=1,
    )
    logger.info('[JOB ADDED] id=%s run_at_utc=%s store="default"', job_id, run_at.isoformat())


# ----------------------------------
# ПЛАНИРОВАНИЕ ДЛЯ ВСЕХ АКТИВНЫХ
# ----------------------------------
async def plan_all_active():
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

    logger.info("[PLAN ALL] active_schedules=%d", len(schedules))
    for sch in schedules:
        await plan_next_for_schedule(sch.id)


# ----------------------------------
# ЖИЗНЕННЫЙ ЦИКЛ ПЛАНИРОВЩИКА
# ----------------------------------
def start_scheduler():
    """
    Запуск APScheduler (один раз при старте приложения).
    """
    if not scheduler.running:
        # Диагностика: слушатели событий + «пульс»
        scheduler.add_listener(
            _on_job_event, EVENT_JOB_EXECUTED | EVENT_JOB_ERROR | EVENT_JOB_MISSED
        )
        scheduler.add_job(
            _heartbeat,
            "interval",
            seconds=60,
            id="__hb__",
            replace_existing=True,
            coalesce=True,
            max_instances=1,
        )
        scheduler.start()
        logger.info("[SCHEDULER STARTED]")