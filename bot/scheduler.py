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
from bot.db_repo.models import (
    User,
    Plant,
    Schedule,
    ActionType,
    ActionSource,
    ActionStatus,
    ScheduleType,
    ActionPending,
    ActionPendingMessage,
)
from bot.db_repo.unit_of_work import new_uow
from bot.services.rules import next_by_interval, next_by_weekly, _compute_interval_anchor_utc

class RemindCb(CallbackData, prefix="r"):
    action: str
    pending_id: int


logger = logging.getLogger(__name__)

SYNC_DB_URL = (
    os.getenv("DATABASE_URL_SYNC")
    or os.getenv("DATABASE_URL", "postgresql+asyncpg://bot:bot@db:5432/watering").replace(
        "+asyncpg", ""
    )
)
jobstores = {"default": SQLAlchemyJobStore(url=SYNC_DB_URL, tablename="apscheduler_jobs")}
scheduler = AsyncIOScheduler(jobstores=jobstores)


def _job_id(schedule_id: int) -> str:
    return f"sch:{schedule_id}"


def _is_interval_type(t: ScheduleType | str) -> bool:
    if isinstance(t, ScheduleType):
        return t is ScheduleType.INTERVAL
    if isinstance(t, str):
        return t.upper() == "INTERVAL"
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



def _build_action_kb_for_pending(pending_id: int, allowed: bool):
    """Возвращает клавиатуру с pending_id, если пользователю разрешено отмечать действия"""
    if not allowed:
        return None
    kb = InlineKeyboardBuilder()
    kb.button(text="✅ Сделано", callback_data=RemindCb(action="done", pending_id=pending_id).pack())
    kb.button(text="⏭️ Пропустить", callback_data=RemindCb(action="skip", pending_id=pending_id).pack())
    kb.adjust(2)
    return kb.as_markup()


async def send_reminder(pending_id: int):
    """Отправка уведомлений владельцу и подписчикам с учётом разрешений. Все записи в БД — через репозитории."""
    logger.info("[JOB START] pending_id=%s", pending_id)
    bot = Bot(token=settings.BOT_TOKEN)

    schedule_id: int | None = None
    commit_ok = False

    try:
        async with new_uow() as uow:

            pending = await uow.action_pendings.get(pending_id)
            if not pending:
                logger.warning("[JOB SKIP] pending_id=%s missing", pending_id)
                return

            sch: Schedule | None = await uow.jobs.get_schedule(pending.schedule_id)
            if not sch or not sch.active:
                logger.warning("[JOB SKIP] schedule_id=%s inactive/missing", getattr(sch, "id", None))
                return

            schedule_id = sch.id

            user: User = sch.plant.user
            plant: Plant = sch.plant

            emoji = sch.action.emoji()
            title = sch.action.title_ru()
            base_text = f"{emoji} {title}: {plant.name}"

            # владелец
            try:
                msg = await bot.send_message(
                    user.id,
                    base_text,
                    reply_markup=_build_action_kb_for_pending(pending.id, True),
                )
                await uow.action_pending_messages.create(
                    pending_id=pending.id,
                    chat_id=user.id,
                    message_id=msg.message_id,
                    is_owner=True,
                    share_id=None,
                    share_member_id=None,
                )
                logger.info(
                    "[SEND OK OWNER] user_id=%s plant_id=%s action=%s pending_id=%s buttons=%s",
                    user.id, plant.id, sch.action, pending.id, True,
                )
            except Exception as e:
                logger.exception("[SEND ERR OWNER] pending_id=%s schedule_id=%s: %s", pending.id, sch.id, e)

            # подписчики
            try:
                shares = await uow.share_links.list_links(sch.id)
            except Exception:
                shares = []
                logger.exception("[SHARE LINKS ERR] schedule_id=%s", sch.id)

            owner_mention = (f"@{user.tg_username}" if user.tg_username else f"id{user.id}")
            sub_text = f"{base_text}\n\n(Уведомление из расписания пользователя {owner_mention})"

            for share in shares:
                if not getattr(share, "is_active", True):
                    continue
                try:
                    members = await uow.share_members.list_active_by_share(share.id)
                except Exception:
                    members = []
                    logger.exception("[SHARE MEMBERS ERR] share_id=%s", share.id)

                for m in members:
                    if getattr(m, "muted", False):
                        continue

                    can_complete = (
                        m.can_complete_override
                        if m.can_complete_override is not None
                        else bool(share.allow_complete_default)
                    )

                    try:
                        msg = await bot.send_message(
                            m.subscriber_user_id,
                            sub_text,
                            reply_markup=_build_action_kb_for_pending(pending.id, can_complete),
                        )
                        await uow.action_pending_messages.create(
                            pending_id=pending.id,
                            chat_id=m.subscriber_user_id,
                            message_id=msg.message_id,
                            is_owner=False,
                            share_id=share.id,
                            share_member_id=m.id,
                        )
                        logger.info(
                            "[SEND OK SUB] user_id=%s share_id=%s schedule_id=%s pending_id=%s buttons=%s",
                            m.subscriber_user_id, share.id, sch.id, pending.id, bool(can_complete),
                        )
                    except Exception as e:
                        logger.exception(
                            "[SEND ERR SUB] schedule_id=%s user_id=%s share_id=%s pending_id=%s: %s",
                            sch.id, m.subscriber_user_id, share.id, pending.id, e,
                        )

            await uow.commit()
            commit_ok = True

    finally:
        try:
            await bot.session.close()
        except Exception:
            logger.exception("[BOT CLOSE ERR] pending_id=%s", pending_id)

    if schedule_id is not None and commit_ok:
        await plan_next_for_schedule(schedule_id)


# bot/scheduler.py
from sqlalchemy.exc import IntegrityError

async def plan_next_for_schedule(
    schedule_id: int,
    *,
    last_override_utc: datetime | None = None,
    run_at_override_utc: datetime | None = None,
):
    async with new_uow() as uow:
        sch = await uow.jobs.get_schedule(schedule_id)
        if not sch or not sch.active:
            # удаляем задачу, если расписание выключено/удалено
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

            if _is_interval_type(sch.type) and not last_db_dt:
                anchor_utc = _compute_interval_anchor_utc(
                    tz_name=tz,
                    local_time=sch.local_time,
                    now_utc=now_utc,
                )
                if getattr(sch, "created_at", None) and anchor_utc < sch.created_at:
                    anchor_utc = sch.created_at
                await manual_done_and_reschedule(schedule_id, done_at_utc=anchor_utc)
                return

            if last_db_dt:
                candidates.append((last_db_dt, last_db_src or ActionSource.SCHEDULE))
            if last_override_utc:
                candidates.append((last_override_utc, ActionSource.MANUAL))

            last_dt, last_src = (max(candidates, key=lambda x: x[0]) if candidates else (None, None))

            run_at = _calc_next_run_utc(
                sch=sch,
                user_tz=tz,
                last_event_utc=last_dt,
                last_event_source=last_src,
                now_utc=now_utc,
            )
        else:
            run_at = run_at_override_utc


        deleted = await uow.action_pendings.delete_future_for_schedule(
            schedule_id=sch.id,
            from_utc=now_utc,
        )
        if deleted:
            logger.info("[PENDING CLEANUP] schedule_id=%s deleted=%s from_utc=%s",
                        sch.id, deleted, now_utc.isoformat())

        try:
            found = await uow.action_pendings.find_by_unique(
                schedule_id=sch.id,
                planned_run_at_utc=run_at,
            )
            if found:
                pending_id = found.id
            else:
                created = await uow.action_pendings.create(
                    schedule_id=sch.id,
                    plant_id=sch.plant.id,
                    owner_user_id=sch.plant.user.id,
                    action=sch.action,
                    planned_run_at_utc=run_at,
                )
                pending_id = created.id if hasattr(created, "id") else int(created)
        except IntegrityError:
            await uow.rollback()
            found = await uow.action_pendings.find_by_unique(
                schedule_id=sch.id,
                planned_run_at_utc=run_at,
            )
            if not found:
                raise
            pending_id = found.id

        await uow.commit()

        # 4) логируем, в том числе локальное время
        try:
            loc = pytz.timezone(tz)
            logger.info(
                "[PLAN] schedule_id=%s user_id=%s plant_id=%s action=%s run_at_utc=%s run_at_local=%s tz=%s pending_id=%s",
                schedule_id, user.id, sch.plant.id, sch.action,
                run_at.isoformat(),
                run_at.astimezone(loc).strftime("%Y-%m-%d %H:%M:%S"),
                tz,
                pending_id,
            )
        except Exception:
            logger.info(
                "[PLAN] schedule_id=%s user_id=%s plant_id=%s action=%s run_at_utc=%s tz=%s pending_id=%s",
                schedule_id, user.id, sch.plant.id, sch.action, run_at.isoformat(), tz, pending_id,
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
        args=[pending_id],
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

    run_at = _calc_next_run_utc(
        sch=sch,
        user_tz=tz,
        last_event_utc=done_at_utc,
        last_event_source=ActionSource.MANUAL,
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
        # scheduler.add_job(
        #     _heartbeat,
        #     "interval",
        #     seconds=60,
        #     id="__hb__",
        #     replace_existing=True,
        #     coalesce=True,
        #     max_instances=1,
        # )
        scheduler.start()
        logger.info("[SCHEDULER STARTED]")