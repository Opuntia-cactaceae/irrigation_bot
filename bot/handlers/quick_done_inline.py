# bot/handlers/quick_done_inline.py
from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Dict, Any

import pytz
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionType
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
def _calc_next_run_utc(*, sch, user_tz: str, last_event_utc: Optional[datetime], now_utc: datetime) -> datetime:
    if sch.type == "interval":
        return next_by_interval(last_event_utc, sch.interval_days, sch.local_time, user_tz, now_utc)
    else:
        return next_by_weekly(last_event_utc, sch.weekly_mask, sch.local_time, user_tz, now_utc)


# ---------- сбор ближайших задач по пользователю ----------
async def _collect_upcoming_for_user(user_tg_id: int, limit: int = 15) -> List[Dict[str, Any]]:
    """
    Возвращает список словарей:
    { 'schedule_id', 'dt_utc', 'dt_local', 'plant_id', 'plant_name', 'action', 'user_tz' }
    отсортированный по времени.
    Делает всё через репозитории, без голого Session.
    """
    async with new_uow() as uow:
        user = await uow.users.get_or_create(user_tg_id)
        user_tz = getattr(user, "tz", "UTC") or "UTC"

        # Берём растения с отношениями (schedules + events). Если такого метода нет — можно заменить на list_by_user.
        try:
            plants = await uow.plants.list_by_user_with_relations(user.id)
        except AttributeError:
            plants = await uow.plants.list_by_user(user.id)

    tz = pytz.timezone(user_tz)
    now_utc = datetime.now(pytz.UTC)
    items: List[Dict[str, Any]] = []

    for p in plants:
        schedules = [s for s in (getattr(p, "schedules", []) or []) if getattr(s, "active", True)]
        if not schedules:
            continue

        events = list(getattr(p, "events", []) or [])

        for sch in schedules:
            # Последнее событие по тому же действию
            last = max(
                (getattr(e, "done_at_utc", None) for e in events if e.action == sch.action),
                default=None,
            )
            run_at_utc = _calc_next_run_utc(sch=sch, user_tz=user_tz, last_event_utc=last, now_utc=now_utc)
            items.append({
                "schedule_id": sch.id,
                "dt_utc": run_at_utc,
                "dt_local": run_at_utc.astimezone(tz),
                "plant_id": p.id,
                "plant_name": p.name,
                "action": sch.action,
                "user_tz": user_tz,
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
        # показываем ЛОКАЛЬНОЕ время пользователя (есть в items)
        t_str = it["dt_local"].strftime("%H:%M")
        lines.append(f"{idx:>2}. {t_str} {emoji} {it['plant_name']} (id:{it['plant_id']})")
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

        # Проверим права и создадим событие
        async with new_uow() as uow:
            sch = await uow.schedules.get(schedule_id)
            if not sch or not getattr(sch, "active", True):
                await cb.answer("Расписание не найдено или отключено", show_alert=True)
                return await show_quick_done_menu(cb)

            plant = await uow.plants.get(getattr(sch, "plant_id", None))
            if not plant:
                await cb.answer("Растение не найдено", show_alert=True)
                return await show_quick_done_menu(cb)

            owner = await uow.users.get_by_id(getattr(plant, "user_id", None))
            if not owner or owner.tg_user_id != cb.from_user.id:
                await cb.answer("Недоступно", show_alert=True)
                return

            # Записываем manual Event
            await uow.events.create(plant_id=plant.id, action=sch.action, source="manual")
            # Коммит произойдёт на выходе из контекста

        # Перепланируем следующее напоминание по этому расписанию
        try:
            await plan_next_for_schedule(cb.bot, schedule_id)
        except Exception:
            # не критично для UX
            pass

        await cb.answer("Отмечено ✅", show_alert=False)
        return await show_quick_done_menu(cb)

    # fallback
    await cb.answer()