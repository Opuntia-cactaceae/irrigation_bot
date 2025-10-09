# bot/scheduler.py
from __future__ import annotations

import logging
import os
from datetime import datetime
from typing import Optional

import pytz

from aiogram import Bot
from aiogram.filters.callback_data import CallbackData
from aiogram.utils.keyboard import InlineKeyboardBuilder
from apscheduler.events import (
    EVENT_JOB_ERROR,
    EVENT_JOB_EXECUTED,
    EVENT_JOB_MISSED,
    JobExecutionEvent,
)
from apscheduler.jobstores.sqlalchemy import SQLAlchemyJobStore
from apscheduler.schedulers.asyncio import AsyncIOScheduler


from bot.config import settings
from bot.db_repo.models import ActionType, Schedule, ScheduleType, User, Plant
from bot.db_repo.models import ActionStatus, ActionSource
from bot.services.rules import next_by_interval, next_by_weekly
from bot.db_repo.unit_of_work import new_uow

class RemindCb(CallbackData, prefix="r"):
    action: str
    schedule_id: int


logger = logging.getLogger(__name__)

SYNC_DB_URL = (
    os.getenv("DATABASE_URL_SYNC")
    or os.getenv("DATABASE_URL", "postgresql+asyncpg://bot:bot@db:5432/watering").replace(
        "+asyncpg", ""
    )
)
jobstores = {"default": SQLAlchemyJobStore(url=SYNC_DB_URL, tablename="apscheduler_jobs")}
scheduler = AsyncIOScheduler(jobstores=jobstores)


ACTION_EMOJI = {
    ActionType.WATERING: "💧",
    ActionType.FERTILIZING: "💊",
    ActionType.REPOTTING: "🪴",
}


def _job_id(schedule_id: int) -> str:
    return f"sch:{schedule_id}"


def _is_interval_type(t) -> bool:
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
    *,
    sch: Schedule,
    user_tz: str,
    last_event_utc: datetime | None,
    last_event_source: Optional["ActionSource"],   # ← добавь это поле
    now_utc: datetime,
) -> datetime:
    if _is_interval_type(sch.type):
        return next_by_interval(
            last_event_utc,
            sch.interval_days,
            sch.local_time,
            user_tz,
            now_utc,
        )
    else:
        return next_by_weekly(
            last_done_utc=last_event_utc,
            last_done_source=last_event_source,
            weekly_mask=sch.weekly_mask,
            local_t=sch.local_time,
            tz_name=user_tz,
            now_utc=now_utc,
        )

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
    try:
        logger.info("[SCHED HEARTBEAT] jobs=%d", len(scheduler.get_jobs()))
    except Exception:
        logger.exception("[SCHED HEARTBEAT] failed")


async def send_reminder(schedule_id: int):
    logger.info("[JOB START] schedule_id=%s", schedule_id)

    bot = Bot(token=settings.BOT_TOKEN)
    try:
        async with new_uow() as uow:
            sch = await uow.jobs.get_schedule(schedule_id)
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

            kb = InlineKeyboardBuilder()
            kb.button(text="✅ Сделано",  callback_data=RemindCb(action="done", schedule_id=schedule_id).pack())
            kb.button(text="⏭️ Пропустить", callback_data=RemindCb(action="skip", schedule_id=schedule_id).pack())
            kb.adjust(2)

            try:
                await bot.send_message(
                    user.id,
                    f"{emoji} {action_text}: {plant.name}",
                    reply_markup=kb.as_markup(),
                )
                logger.info("[SEND OK] user_id=%s plant_id=%s action=%s", user.id, plant.id, sch.action)
            except Exception as e:
                logger.exception("[SEND ERR] schedule_id=%s: %s", schedule_id, e)

    finally:
        await bot.session.close()

    await plan_next_for_schedule(schedule_id)


async def plan_next_for_schedule(
    schedule_id: int,
    *,
    last_override_utc: datetime | None = None,
    run_at_override_utc: datetime | None = None,
):
    async with new_uow() as uow:
        sch = await uow.jobs.get_schedule(schedule_id)
        if not sch or not sch.active:
            try:
                scheduler.remove_job(_job_id(schedule_id))
                logger.info("[JOB REMOVED] schedule_id=%s", schedule_id)
            except Exception:
                pass
            return

        user = sch.plant.user
        tz = user.tz
        now_utc = datetime.now(tz=pytz.UTC)

        if run_at_override_utc is None:
            last_db_dt, last_db_src = await uow.action_logs.last_effective_done(sch.id)
            candidates: list[tuple[datetime, ActionSource]] = []
            if last_db_dt:
                candidates.append((last_db_dt, last_db_src or ActionSource.SCHEDULE))
            if last_override_utc:
                candidates.append((last_override_utc, ActionSource.MANUAL))

            last_dt, last_src = (max(candidates, key=lambda x: x[0]) if candidates else (None, None))

            if _is_interval_type(sch.type):
                run_at = next_by_interval(last_dt, sch.interval_days, sch.local_time, tz, now_utc)
            else:
                run_at = next_by_weekly(
                    last_done_utc=last_dt,
                    last_done_source=last_src,
                    weekly_mask=sch.weekly_mask,
                    local_t=sch.local_time,
                    tz_name=tz,
                    now_utc=now_utc,
                )
        else:
            run_at = run_at_override_utc

        try:
            loc = pytz.timezone(tz)
            logger.info(
                "[PLAN] schedule_id=%s user_id=%s plant_id=%s action=%s run_at_utc=%s run_at_local=%s tz=%s",
                schedule_id, user.id, sch.plant.id, sch.action,
                run_at.isoformat(),
                run_at.astimezone(loc).strftime("%Y-%m-%d %H:%M:%S"),
                tz,
            )
        except Exception:
            logger.info(
                "[PLAN] schedule_id=%s user_id=%s plant_id=%s action=%s run_at_utc=%s tz=%s",
                schedule_id, user.id, sch.plant.id, sch.action, run_at.isoformat(), tz,
            )

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
        args=[schedule_id],
        jobstore="default",
        replace_existing=True,
        misfire_grace_time=3600,
        coalesce=True,
        max_instances=1,
    )
    logger.info('[JOB ADDED] id=%s run_at_utc=%s store="default"', job_id, run_at.isoformat())

async def manual_done_and_reschedule(schedule_id: int, *, done_at_utc: datetime | None = None):
    if done_at_utc is None:
        done_at_utc = datetime.now(tz=pytz.UTC)

    async with new_uow() as uow:
        sch = await uow.schedules.get(schedule_id)
        if not sch or not getattr(sch, "active", True):
            return

        plant = await uow.plants.get(sch.plant_id)
        user  = await uow.users.get(plant.user_id) if plant else None
        tz    = user.tz if user and getattr(user, "tz", None) else "UTC"

        await uow.action_logs.create_manual(
            user=user,
            plant=plant,
            schedule=sch,
            action=sch.action,
            status=ActionStatus.DONE,
            done_at_utc=done_at_utc,
        )

    if _is_interval_type(sch.type):
        run_at = next_by_interval(
            done_at_utc, sch.interval_days, sch.local_time, tz, done_at_utc
        )
    else:
        run_at = next_by_weekly(
            last_done_utc=done_at_utc,
            last_done_source=ActionSource.MANUAL,
            weekly_mask=sch.weekly_mask,
            local_t=sch.local_time,
            tz_name=tz,
            now_utc=done_at_utc,
        )

    await plan_next_for_schedule(schedule_id, run_at_override_utc=run_at)



async def plan_all_active():
    async with new_uow() as uow:
        schedules = await uow.jobs.get_active_schedules()
        logger.info("[PLAN ALL] active_schedules=%d", len(schedules))
        for sch in schedules:
            await plan_next_for_schedule(sch.id)


def start_scheduler():
    if not scheduler.running:
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