# bot/handlers/schedule_delete_inline.py
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionType
from bot.scheduler import scheduler as aps  # для снятия APS job

delete_router = Router(name="schedule_delete_inline")

PREFIX = "sdel"
PAGE_SIZE = 8
WEEK_EMOJI = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]

ACT_EMOJI = {
    ActionType.WATERING: "💧",
    ActionType.FERTILIZING: "💊",
    ActionType.REPOTTING: "🪴",
}


# --------- utils ---------- #
def _slice(items, page: int, size: int):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    return items[(page - 1) * size:(page - 1) * size + size], page, pages, total


def _job_id(schedule_id: int) -> str:
    return f"sch:{schedule_id}"


def _fmt_schedule(s) -> str:
    s_type = getattr(s.type, "value", s.type)
    if s_type == "interval":
        return f"⏱ каждые {getattr(s,'interval_days', '?')} дн в {s.local_time.strftime('%H:%M')}"
    else:
        mask = int(getattr(s, "weekly_mask", 0) or 0)
        days = [lbl for i, lbl in enumerate(WEEK_EMOJI) if mask & (1 << i)]
        days_txt = ",".join(days) if days else "—"
        return f"🗓 {days_txt} в {s.local_time.strftime('%H:%M')}"


async def _list_user_schedules(user_tg_id: int):
    """Вернёт все расписания пользователя с проставленным _plant_name."""
    async with new_uow() as uow:
        me = await uow.users.get_or_create(user_tg_id)
        try:
            plants = await uow.plants.list_by_user(me.id)
        except AttributeError:
            plants = []
        out = []
        for p in plants:
            try:
                items = await uow.schedules.list_by_plant(p.id)
            except AttributeError:
                items = list(getattr(p, "schedules", []) or [])
            for s in items:
                setattr(s, "_plant_name", getattr(p, "name", f"#{p.id}"))
                out.append(s)
    out.sort(key=lambda x: getattr(x, "id", 0), reverse=True)
    return out


# --------- screens ---------- #
async def _screen_list(cb: types.CallbackQuery, page: int = 1):
    items = await _list_user_schedules(cb.from_user.id)
    page_items, page, pages, _ = _slice(items, page, PAGE_SIZE)

    title = "🗑 Удаление расписаний\nНажмите номер, чтобы удалить (будет подтверждение)."
    kb = InlineKeyboardBuilder()

    if page_items:
        start_num = (page - 1) * PAGE_SIZE + 1
        for idx, s in enumerate(page_items, start=start_num):
            plant = getattr(s, "_plant_name", "—")
            emoji = ACT_EMOJI.get(getattr(s, "action", None), "•")
            info = f"{emoji} {plant} · {_fmt_schedule(s)}"
            kb.row(
                types.InlineKeyboardButton(text=f"{idx}", callback_data=f"{PREFIX}:ask:{s.id}:{page}"),
                types.InlineKeyboardButton(text=info, callback_data=f"{PREFIX}:noop"),
            )
    else:
        kb.button(text="(расписаний нет)", callback_data=f"{PREFIX}:noop")
        kb.adjust(1)

    kb.row(
        types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:pg:{max(1, page-1)}"),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:pg:{min(pages, page+1)}"),
    )
    kb.row(
        types.InlineKeyboardButton(text="📅 К календарю", callback_data="cal:feed:upc:1:all:0"),
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )

    await cb.message.edit_text(title, reply_markup=kb.as_markup())
    await cb.answer()


# --------- handlers ---------- #
@delete_router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_delete_callbacks(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "noop"

    if action == "noop":
        return await cb.answer()

    if action in ("list", "pg"):
        page = int(parts[2]) if len(parts) > 2 else 1
        return await _screen_list(cb, page)

    if action == "ask":
        try:
            sch_id = int(parts[2]); page = int(parts[3]) if len(parts) > 3 else 1
        except Exception:
            return await cb.answer("Не получилось", show_alert=True)

        kb = InlineKeyboardBuilder().row(
            types.InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"{PREFIX}:yes:{sch_id}:{page}"),
            types.InlineKeyboardButton(text="↩️ Нет", callback_data=f"{PREFIX}:list:{page}"),
        )
        await cb.message.edit_text("Удалить это расписание? Это действие необратимо.", reply_markup=kb.as_markup())
        return await cb.answer()

    if action == "yes":
        try:
            sch_id = int(parts[2]); page = int(parts[3]) if len(parts) > 3 else 1
        except Exception:
            return await cb.answer("Не получилось удалить", show_alert=True)

        async with new_uow() as uow:
            try:
                await uow.schedules.delete(sch_id)
            except AttributeError:
                try:
                    await uow.schedules.update(sch_id, active=False)
                except AttributeError:
                    pass

        # снять APS-задачу (если была)
        try:
            aps.remove_job(_job_id(sch_id))
        except Exception:
            pass

        await cb.answer("Удалено 🗑", show_alert=False)
        return await _screen_list(cb, page)

    await cb.answer()