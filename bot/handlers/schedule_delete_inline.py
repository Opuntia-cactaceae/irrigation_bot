# bot/handlers/schedule_delete_inline.py
from __future__ import annotations

from typing import List, Dict, Any, Optional

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionType
from bot.scheduler import scheduler as aps  # для снятия APS job

delete_router = Router(name="schedule_delete_inline")
PREFIX = "sdel"

PAGE_SIZE = 12
WEEK_EMOJI = ["Пн", "Вт", "Ср", "Чт", "Пт", "Сб", "Вс"]
ACTION_EMOJI = {
    ActionType.WATERING: "💧",
    ActionType.FERTILIZING: "💊",
    ActionType.REPOTTING: "🪴",
}


# -------- utils -------- #
def _slice(items, page: int, size: int):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    return items[(page - 1) * size:(page - 1) * size + size], page, pages, total


def _job_id(schedule_id: int) -> str:
    return f"sch:{schedule_id}"


def _fmt_schedule(s) -> str:
    """Короткая строка о расписании."""
    s_type = getattr(s.type, "value", s.type)
    if s_type == "interval":
        return f"⏱ каждые {getattr(s,'interval_days','?')} дн в {s.local_time.strftime('%H:%M')}"
    else:
        mask = int(getattr(s, "weekly_mask", 0) or 0)
        days = [lbl for i, lbl in enumerate(WEEK_EMOJI) if mask & (1 << i)]
        days_txt = ",".join(days) if days else "—"
        return f"🗓 {days_txt} в {s.local_time.strftime('%H:%M')}"


async def _collect_all_schedules(user_tg_id: int) -> List[Dict[str, Any]]:
    """Все расписания пользователя, с именем растения и эмодзи действия."""
    result: List[Dict[str, Any]] = []
    async with new_uow() as uow:
        me = await uow.users.get_or_create(user_tg_id)
        try:
            plants = await uow.plants.list_by_user_with_relations(me.id)
        except AttributeError:
            plants = await uow.plants.list_by_user(me.id)

    for p in plants:
        for s in (getattr(p, "schedules", []) or []):
            result.append({
                "id": s.id,
                "plant_id": p.id,
                "plant_name": p.name,
                "action": s.action,
                "desc": _fmt_schedule(s),
            })

    # свежие сверху
    result.sort(key=lambda x: x["id"], reverse=True)
    return result


# -------- screens -------- #
async def show_delete_menu(target: types.Message | types.CallbackQuery, page: int = 1):
    """Нумерованный список в тексте + кнопки «Удалить №…»."""
    if isinstance(target, types.CallbackQuery):
        message = target.message
        user_id = target.from_user.id
    else:
        message = target
        user_id = target.from_user.id

    items = await _collect_all_schedules(user_id)
    page_items, page, pages, total = _slice(items, page, PAGE_SIZE)

    kb = InlineKeyboardBuilder()
    lines: List[str] = ["🗑 <b>Удаление расписаний</b>"]

    if not total:
        lines.append("У вас пока нет расписаний.")
        kb.row(
            types.InlineKeyboardButton(text="📅 К календарю", callback_data="cal:feed:upc:1:all:0"),
            types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
        )
        text = "\n".join(lines)
        if isinstance(target, types.CallbackQuery):
            await message.edit_text(text, reply_markup=kb.as_markup())
            await target.answer()
        else:
            await message.answer(text, reply_markup=kb.as_markup())
        return

    lines.append("Нажмите на кнопку под списком, чтобы удалить нужный номер.")

    start_num = (page - 1) * PAGE_SIZE + 1
    for idx, it in enumerate(page_items, start=start_num):
        emoji = ACTION_EMOJI.get(it["action"], "•")
        lines.append(f"{idx:>2}. {emoji} {it['plant_name']} · {it['desc']}")
        kb.row(
            types.InlineKeyboardButton(
                text=f"🗑 Удалить №{idx}",
                callback_data=f"{PREFIX}:ask:{it['id']}:{page}",
            )
        )

    # пагинация
    kb.row(
        types.InlineKeyboardButton(text="◀️", callback_data=f"{PREFIX}:pg:{max(1, page-1)}"),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(text="▶️", callback_data=f"{PREFIX}:pg:{min(pages, page+1)}"),
    )
    kb.row(
        types.InlineKeyboardButton(text="📅 К календарю", callback_data="cal:feed:upc:1:all:0"),
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )

    text = "\n".join(lines)
    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text, reply_markup=kb.as_markup())
        await target.answer()
    else:
        await message.answer(text, reply_markup=kb.as_markup())


async def _screen_confirm(cb: types.CallbackQuery, sch_id: int, page: int):
    """Экран подтверждения."""
    # можно достать ещё раз описание — для надёжности
    desc_line = f"#{sch_id}"
    try:
        async with new_uow() as uow:
            s = await uow.schedules.get(sch_id)
            if s:
                # ищем имя растения
                p = await uow.plants.get(getattr(s, "plant_id", None))
                plant = getattr(p, "name", f"#{getattr(s, 'plant_id', '?')}")
                emoji = ACTION_EMOJI.get(getattr(s, "action", None), "•")
                desc_line = f"{emoji} {plant} · {_fmt_schedule(s)}"
    except Exception:
        pass

    text = "Подтвердите удаление:\n" + desc_line
    kb = InlineKeyboardBuilder().row(
        types.InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"{PREFIX}:yes:{sch_id}:{page}"),
        types.InlineKeyboardButton(text="↩️ Нет", callback_data=f"{PREFIX}:list:{page}"),
    )
    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()


# -------- handlers -------- #
@delete_router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_delete_callbacks(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "noop"

    if action == "noop":
        return await cb.answer()

    if action in ("list", "pg"):
        page = int(parts[2]) if len(parts) > 2 else 1
        return await show_delete_menu(cb, page)

    if action == "ask":
        sch_id = int(parts[2]); page = int(parts[3]) if len(parts) > 3 else 1
        return await _screen_confirm(cb, sch_id, page)

    if action == "yes":
        sch_id = int(parts[2]); page = int(parts[3]) if len(parts) > 3 else 1

        async with new_uow() as uow:
            try:
                await uow.schedules.delete(sch_id)
            except AttributeError:
                try:
                    await uow.schedules.update(sch_id, active=False)
                except AttributeError:
                    pass

        try:
            aps.remove_job(_job_id(sch_id))
        except Exception:
            pass

        await cb.answer("Удалено 🗑", show_alert=False)
        return await show_delete_menu(cb, page)

    await cb.answer()