# bot/handlers/quick_done_inline.py
from __future__ import annotations
from datetime import datetime
from typing import List, Optional, Dict, Any

import pytz
from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionType, ScheduleType, ActionSource
from bot.scheduler import manual_done_and_reschedule, _calc_next_run_utc

router = Router(name="quick_done_inline")
PREFIX = "qdone"

WEEK_RU = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]


def _as_value(x):
    return getattr(x, "value", x)


def _fmt_schedule(s) -> str:
    s_type = _as_value(getattr(s, "type", None))
    if s_type == ScheduleType.INTERVAL:
        return f"каждые {getattr(s, 'interval_days', '?')} дн в {s.local_time.strftime('%H:%M')}"
    else:
        mask = int(getattr(s, "weekly_mask", 0) or 0)
        days = [lbl for i, lbl in enumerate(WEEK_RU) if mask & (1 << i)]
        days_txt = ",".join(days) if days else "—"
        return f"{days_txt} в {s.local_time.strftime('%H:%M')}"

def _fmt_date_label(dt_local: datetime) -> str:
    """
    Возвращает дату в формате: "Ср 09.10"
    """
    dow = WEEK_RU[dt_local.weekday()]
    return f"{dow} {dt_local.day:02d}.{dt_local.month:02d}"


def _fmt_tail_for_line(
    *,
    s_type,
    weekly_mask: int | None,
    interval_days: int | None,
    dt_local: datetime,
) -> str:
    s_val = getattr(s_type, "value", s_type)
    interval_val = getattr(ScheduleType.INTERVAL, "value", ScheduleType.INTERVAL)

    if s_val == ScheduleType.INTERVAL or s_val == interval_val:
        d = int(interval_days or 0)
        return f"каждые {d} дн" if d > 0 else ""

    # weekly
    mask = int(weekly_mask or 0)
    if mask == 0:
        return ""

    days = [lbl for i, lbl in enumerate(WEEK_RU) if mask & (1 << i)]
    if len(days) == 1 and WEEK_RU[dt_local.weekday()] == days[0]:
        return ""

    return ",".join(days)

def _as_action(x) -> ActionType | None:
    return ActionType.from_any(x)

async def _collect_upcoming_for_user(user_tg_id: int, limit: int = 15) -> List[Dict[str, Any]]:
    async with new_uow() as uow:
        user = await uow.users.get(user_tg_id)
        user_tz = getattr(user, "tz", "UTC") or "UTC"

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

            for sch in schedules:
                last_event_utc, last_event_source = await uow.action_logs.last_effective_done(sch.id)

                run_at_utc = _calc_next_run_utc(
                    sch=sch,
                    user_tz=user.tz,
                    last_event_utc=last_event_utc,
                    last_event_source=last_event_source,
                    now_utc=now_utc,
                )
                run_local = run_at_utc.astimezone(tz)

                items.append({
                    "schedule_id": sch.id,
                    "dt_utc": run_at_utc,
                    "dt_local": run_local,
                    "plant_id": p.id,
                    "plant_name": p.name,
                    "action": sch.action,
                    "user_tz": user_tz,
                    "desc": _fmt_schedule(sch),
                    "s_type": getattr(sch, "type", None),
                    "weekly_mask": int(getattr(sch, "weekly_mask", 0) or 0),
                    "interval_days": getattr(sch, "interval_days", None),
                })

    items.sort(key=lambda x: x["dt_utc"])
    return items[:limit]


async def show_quick_done_menu(target: types.Message | types.CallbackQuery):
    print("show_quick_done_menu")
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

    lines = ["✅ <b>Отметить выполнение</b>", "Ближайшие задачи:"]
    kb = InlineKeyboardBuilder()

    for idx, it in enumerate(items, start=1):
        at = ActionType.from_any(it["action"])
        emoji = at.emoji() if at else "•"

        date_lbl = _fmt_date_label(it["dt_local"])
        t_str = it["dt_local"].strftime("%H:%M")

        tail = _fmt_tail_for_line(
            s_type=it.get("s_type"),
            weekly_mask=it.get("weekly_mask"),
            interval_days=it.get("interval_days"),
            dt_local=it["dt_local"],
        )

        if tail:
            line = f"{idx:>2}. {date_lbl} {t_str} {emoji} {it['plant_name']} {tail}"
        else:
            line = f"{idx:>2}. {date_lbl} {t_str} {emoji} {it['plant_name']}"
        lines.append(line)

        kb.row(
            types.InlineKeyboardButton(
                text=f"✅ Отметить №{idx}",
                callback_data=f"{PREFIX}:done:{it['schedule_id']}"
            )
        )

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


@router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_quick_done_callbacks(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "noop"

    if action == "noop":
        return await cb.answer()

    if action == "refresh":
        return await show_quick_done_menu(cb)

    if action == "done":
        try:
            schedule_id = int(parts[2])
        except Exception:
            return await cb.answer("Не получилось отметить", show_alert=True)

        async with new_uow() as uow:
            sch = await uow.schedules.get(schedule_id)
            if not sch or not getattr(sch, "active", True):
                await cb.answer("Расписание не найдено или отключено", show_alert=True)
                return await show_quick_done_menu(cb)

            plant = await uow.plants.get(getattr(sch, "plant_id", None))
            if not plant:
                await cb.answer("Растение не найдено", show_alert=True)
                return await show_quick_done_menu(cb)

            me = await uow.users.get(cb.from_user.id)
            if getattr(plant, "user_id", None) != getattr(me, "id", None):
                await cb.answer("Недоступно", show_alert=True)
                return

        try:
            print("in")
            await manual_done_and_reschedule(schedule_id)
        except Exception:
            raise

        await cb.answer("Отмечено ✅", show_alert=False)
        return await show_quick_done_menu(cb)

    await cb.answer()