# bot/handlers/quick_done_inline.py
from __future__ import annotations
from datetime import datetime
from typing import List, Optional

import pytz
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.base import AsyncSessionLocal
from bot.db_repo.models import Schedule, Plant, User, Event, ActionType
from bot.db_repo.unit_of_work import new_uow
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from bot.services.rules import next_by_interval, next_by_weekly
from bot.scheduler import plan_next_for_schedule

router = Router(name="quick_done_inline")
PREFIX = "qdone"

ACTION_EMOJI = {
    ActionType.WATERING: "💧",
    ActionType.FERTILIZING: "💊",
    ActionType.REPOTTING: "🪴",
}

# ---------- утилита расчёта ближайшего наступления по расписанию ----------
def _calc_next_run_utc(*, sch: Schedule, user_tz: str, last_event_utc: Optional[datetime], now_utc: datetime) -> datetime:
    if sch.type == "interval":
        return next_by_interval(last_event_utc, sch.interval_days, sch.local_time, user_tz, now_utc)
    else:
        return next_by_weekly(last_event_utc, sch.weekly_mask, sch.local_time, user_tz, now_utc)

# ---------- сбор ближайших задач по пользователю ----------
async def _collect_upcoming_for_user(user_tg_id: int, limit: int = 15):
    """
    Возвращает список словарей:
    { 'schedule_id', 'dt_utc', 'plant_id', 'plant_name', 'action' }
    отсортированный по времени.
    """
    async with AsyncSessionLocal() as session:
        # user + все активные расписания с связями
        q = (
            select(Schedule)
            .join(Schedule.plant)
            .join(Plant.user)
            .where(User.tg_user_id == user_tg_id, Schedule.active.is_(True))
            .options(
                selectinload(Schedule.plant).selectinload(Plant.user),
                selectinload(Schedule.plant).selectinload(Plant.events),
            )
        )
        schedules: List[Schedule] = (await session.execute(q)).scalars().all()

    items = []
    now_utc = datetime.now(pytz.UTC)
    for sch in schedules:
        user = sch.plant.user
        tz = user.tz
        last = max((e.done_at_utc for e in (sch.plant.events or []) if e.action == sch.action), default=None)
        run_at = _calc_next_run_utc(sch=sch, user_tz=tz, last_event_utc=last, now_utc=now_utc)
        items.append({
            "schedule_id": sch.id,
            "dt_utc": run_at,
            "plant_id": sch.plant.id,
            "plant_name": sch.plant.name,
            "action": sch.action,
        })

    items.sort(key=lambda x: x["dt_utc"])
    return items[:limit]

# ---------- публичный вход из главного меню ----------
async def show_quick_done_menu(target: types.Message | types.CallbackQuery):
    """
    Показывает список ближайших задач с кнопками «✅».
    """
    if isinstance(target, types.CallbackQuery):
        message = target.message
        user_id = target.from_user.id
    else:
        message = target
        user_id = target.from_user.id

    items = await _collect_upcoming_for_user(user_id)

    if not items:
        kb = InlineKeyboardBuilder()
        kb.row(
            types.InlineKeyboardButton(text="🗓️ Создать расписание", callback_data="cal:plan:upc:1:all:0"),
            types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
        )
        text = "Пока нет запланированных задач.\nСоздайте расписание, чтобы видеть ближайшие действия."
        if isinstance(target, types.CallbackQuery):
            await message.edit_text(text, reply_markup=kb.as_markup())
            await target.answer()
        else:
            await message.answer(text, reply_markup=kb.as_markup())
        return

    # Собираем текст и клавиатуру
    lines = ["✅ <b>Отметить выполнено</b>", "Ближайшие задачи:"]
    kb = InlineKeyboardBuilder()

    for idx, it in enumerate(items, start=1):
        emoji = ACTION_EMOJI.get(it["action"], "•")
        # покажем локальное время пользователя
        # локаль берём из первого элемента (у всех один и тот же user); чтобы не делать лишний запрос, форматируем в UTC с пометкой
        # лучше всего — хранить tz пользователя в items, но для простоты отобразим HH:MM UTC
        t_str = it["dt_utc"].strftime("%H:%M")
        lines.append(f"{idx:>2}. {t_str} {emoji} {it['plant_name']} (id:{it['plant_id']})")
        # Кнопка «✅» для этого пункта
        kb.row(
            types.InlineKeyboardButton(
                text=f"✅ {idx}. Отметить",
                callback_data=f"{PREFIX}:done:{it['schedule_id']}"
            )
        )

    # низ экрана
    kb.row(
        types.InlineKeyboardButton(text="🔄 Обновить", callback_data=f"{PREFIX}:refresh"),
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )

    text = "\n".join(lines)
    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text, reply_markup=kb.as_markup())
        await target.answer()
    else:
        await message.answer(text, reply_markup=kb.as_markup())

# ---------- обработчики колбэков ----------
@router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_quick_done_callbacks(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "noop"

    if action == "noop":
        return await cb.answer()

    if action == "refresh":
        return await show_quick_done_menu(cb)

    if action == "done":
        # qdone:done:<schedule_id>
        try:
            schedule_id = int(parts[2])
        except Exception:
            return await cb.answer("Не получилось отметить", show_alert=True)

        # Найдём расписание и создадим событие по его растению/действию
        async with AsyncSessionLocal() as session:
            sch: Schedule | None = await session.get(
                Schedule,
                schedule_id,
                options=(
                    selectinload(Schedule.plant),
                ),
            )
            if not sch:
                await cb.answer("Расписание не найдено", show_alert=True)
                return await show_quick_done_menu(cb)

        async with new_uow() as uow:
            # записываем manual Event
            await uow.events.create(plant_id=sch.plant.id, action=sch.action, source="manual")

        # перепланируем следующее напоминание по этому расписанию
        await plan_next_for_schedule(cb.bot, schedule_id)

        await cb.answer("Отмечено ✅", show_alert=False)
        return await show_quick_done_menu(cb)

    # fallback
    await cb.answer()