# bot/handlers/share_codes_inline.py
from __future__ import annotations

from typing import Optional, Dict, List, Tuple, Any
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import ActionType, Schedule, Plant, User, ScheduleType
from bot.services.cal_shared import (
    ACTION_TO_EMOJI,
    WEEK_RU,
    format_schedule_line,
)

codes_router = Router(name="share_codes_inline")
PREFIX = "codes"
PAGE_SIZE = 7


def _slice(items: list, page: int, size: int = PAGE_SIZE):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    s, e = (page - 1) * size, (page - 1) * size + size
    return items[s:e], page, pages, total

def _dt_local_for_sched(s: Schedule, tz_name: str) -> datetime:
    """
    Собирает dt_local (локальный datetime) из поля Schedule.local_time.
    Используется для format_schedule_line(...).
    """
    tz = ZoneInfo(tz_name)

    if isinstance(s.local_time, time):
        now = datetime.now(tz)
        return now.replace(
            hour=s.local_time.hour,
            minute=s.local_time.minute,
            second=0,
            microsecond=0,
        )

    now = datetime.now(tz)
    return now.replace(hour=0, minute=0, second=0, microsecond=0)



@dataclass
class ShareCode:
    code: str
    owner_tg_id: int
    action: Optional[ActionType]  # None => все действия
    title: Optional[str] = None
    created_at: Optional[datetime] = None
    expires_at: Optional[datetime] = None






async def _list_user_codes(tg_id: int) -> List[ShareCode]:
    """
    Возвращает список ShareCode, созданных пользователем.
    Определяет action по прикреплённым расписаниям: если у всех одно и то же — ставим его, иначе None.
    """
    async with new_uow() as uow:
        links = await uow.share_links.list_by_owner(tg_id, with_relations=True)

    result: List[ShareCode] = []
    for link in links:
        actions = {
            getattr(ls.schedule, "action", None)
            for ls in getattr(link, "schedules", []) if getattr(ls, "schedule", None)
        }
        deduced_action = next(iter(actions)) if len(actions) == 1 else None

        result.append(
            ShareCode(
                code=link.code,
                owner_tg_id=link.owner_user_id,
                action=deduced_action,
                title=link.title,
                created_at=getattr(link, "created_at_utc", None),
                expires_at=getattr(link, "expires_at_utc", None),
            )
        )

    return result


async def _list_schedules_for_code(tg_id: int, code: ShareCode) -> List[Schedule]:
    async with new_uow() as uow:
        link = await uow.share_links.get_by_code(code.code)
        if not link or link.owner_user_id != tg_id:
            return []

        pairs = await uow.share_links.list_link_schedules([link.id])
        schedule_ids = [p.schedule_id for p in pairs]
        if not schedule_ids:
            return []

        if code.action is not None:
            schedules = await uow.schedules.list_active_by_ids(schedule_ids, action=code.action)
        else:
            schedules = await uow.schedules.list_active_by_ids(schedule_ids)

    return schedules


async def _plants_by_id(plant_ids: List[int]) -> Dict[int, Plant]:
    ids = list({int(pid) for pid in plant_ids if pid})
    if not ids:
        return {}

    async with new_uow() as uow:
        plants = await uow.plants.list_by_ids(ids)

    return {p.id: p for p in plants}



def _code_header_line(c: ShareCode) -> str:
    if c.action:
        emoji = ACTION_TO_EMOJI.get(c.action, "🔖")
        act_txt = f"{emoji} {getattr(c.action, 'label', lambda: c.action.value)() if hasattr(c.action,'label') else c.action.value}"
    else:
        act_txt = "Все действия"
    parts = [f"<code>{c.code}</code>", act_txt]
    if c.title:
        parts.append(f"«{c.title}»")
    return " · ".join(parts)


def _schedule_line_via_formatter(
    *,
    s: Schedule,
    plant_name: str,
    tz_name: str,
    global_idx: int | None,
) -> str:
    """
    Обёртка, которая готовит параметры и зовёт format_schedule_line(...)
    Используем режим 'delete' — он выдаёт краткое «🗓/⏱ ... в HH:MM»
    """
    dt_local = _dt_local_for_sched(s, tz_name)
    action = getattr(s, "action", None)
    s_type = getattr(s, "type", None) or getattr(s, "s_type", None)
    weekly_mask = getattr(s, "weekly_mask", None)
    interval_days = getattr(s, "interval_days", None)

    return format_schedule_line(
        idx=global_idx,
        plant_name=plant_name,
        action=action,
        dt_local=dt_local,
        s_type=s_type,
        weekly_mask=weekly_mask,
        interval_days=interval_days,
        mode="delete",
    )


def _page_nav_kb(prefix_cb: str, page: int, pages: int) -> InlineKeyboardBuilder:
    kb = InlineKeyboardBuilder()
    left_disabled = page <= 1
    right_disabled = page >= pages
    kb.row(
        types.InlineKeyboardButton(
            text="◀️" if not left_disabled else "·",
            callback_data=f"{prefix_cb}:{page-1}" if not left_disabled else f"{PREFIX}:noop",
        ),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(
            text="▶️" if not right_disabled else "·",
            callback_data=f"{prefix_cb}:{page+1}" if not right_disabled else f"{PREFIX}:noop",
        ),
    )
    return kb


# ---------- хендлеры ----------

@codes_router.callback_query(F.data == f"{PREFIX}:noop")
async def on_noop(cb: types.CallbackQuery):
    await cb.answer()


@codes_router.callback_query(F.data == f"{PREFIX}:root")
async def on_codes_root(cb: types.CallbackQuery):
    tg_id = cb.from_user.id
    codes = await _list_user_codes(tg_id)
    await _render_codes_page(cb, codes, page=1)


@codes_router.callback_query(F.data.startswith(f"{PREFIX}:page:"))
async def on_codes_page(cb: types.CallbackQuery):
    _, _, page_str = cb.data.partition(f"{PREFIX}:page:")
    try:
        page = int(page_str)
    except Exception:
        page = 1

    tg_id = cb.from_user.id
    codes = await _list_user_codes(tg_id)
    await _render_codes_page(cb, codes, page=page)


async def _render_codes_page(cb: types.CallbackQuery, codes: List[ShareCode], page: int):
    sliced, page, pages, total = _slice(codes, page, PAGE_SIZE)

    kb = InlineKeyboardBuilder()
    lines = ["🔗 <b>Мои коды доступа</b>"]
    if not total:
        lines.append("Пока нет созданных кодов.")
    else:
        for i, c in enumerate(sliced, 1):
            lines.append(f"{i}. {_code_header_line(c)}")
            kb.row(
                types.InlineKeyboardButton(text=f"👁 Состав {i}", callback_data=f"{PREFIX}:view:{c.code}:1"),
                types.InlineKeyboardButton(text=f"🗑 Удалить {i}", callback_data=f"{PREFIX}:delete:{c.code}"),
            )

    # Навигация
    nav = _page_nav_kb(f"{PREFIX}:page", page, pages)
    kb.attach(nav)

    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data="settings:menu"))

    await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup(), parse_mode="HTML")
    await cb.answer()


@codes_router.callback_query(F.data.startswith(f"{PREFIX}:view:"))
async def on_code_view(cb: types.CallbackQuery):
    # формат: codes:view:{code}:{page}
    parts = cb.data.split(":")
    code = parts[2]
    try:
        page = int(parts[3])
    except Exception:
        page = 1

    tg_id = cb.from_user.id
    codes = await _list_user_codes(tg_id)
    code_obj = next((c for c in codes if c.code == code), None)
    if not code_obj:
        await cb.answer("Код не найден", show_alert=True)
        return

    schedules = await _list_schedules_for_code(tg_id, code_obj)
    plant_ids = list({int(getattr(s, "plant_id", 0) or 0) for s in schedules if getattr(s, "plant_id", None)})
    plants = await _plants_by_id(plant_ids)

    sliced, page, pages, total = _slice(schedules, page, PAGE_SIZE)

    lines = [f"👁 <b>Состав кода доступа</b>"]
    code_info = f"Код: <code>{code_obj.code}</code>"
    if code_obj.title:
        code_info += f" «{code_obj.title}»"
    lines.append(code_info)
    if code_obj.action:
        act_name = getattr(code_obj.action, "label", lambda: code_obj.action.value)() \
                   if hasattr(code_obj.action, "label") else code_obj.action.value
        lines.append(f"Фильтр: {ACTION_TO_EMOJI.get(code_obj.action, '🔖')} {act_name}")
    else:
        lines.append("Фильтр: все действия")
    lines.append("")

    if not total:
        lines.append("В этот код пока не попало ни одного расписания.")
    else:
        async with new_uow() as uow:
            user = await uow.users.get(tg_id)
            tz_name = getattr(user, "tz", None) or "UTC"
        start_num = (page - 1) * PAGE_SIZE + 1
        for idx, s in enumerate(sliced, start=start_num):
            pid = int(getattr(s, "plant_id", 0) or 0)
            plant = plants.get(pid)
            pname = getattr(plant, "name", f"Растение #{pid}") if plant else f"Растение #{pid}"
            line = format_schedule_line(
                idx=idx,
                plant_name=pname,
                action=getattr(s, "action", None),
                dt_local=_dt_local_for_sched(s, tz_name),
                s_type=getattr(s, "type", None) or getattr(s, "s_type", None),
                weekly_mask=getattr(s, "weekly_mask", None),
                interval_days=getattr(s, "interval_days", None),
                mode="delete",
            )
            lines.append(line)

    kb = InlineKeyboardBuilder()
    nav = _page_nav_kb(f"{PREFIX}:view:{code}", page, pages)
    kb.attach(nav)
    kb.row(types.InlineKeyboardButton(text="⬅️ К списку кодов", callback_data=f"{PREFIX}:page:1"))

    await cb.message.edit_text("\n".join(lines), reply_markup=kb.as_markup(), parse_mode="HTML")
    await cb.answer()


@codes_router.callback_query(F.data.startswith(f"{PREFIX}:delete:"))
async def on_code_delete(cb: types.CallbackQuery):
    code = cb.data.split(":")[2]
    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(text="✅ Да, удалить", callback_data=f"{PREFIX}:delete_confirm:{code}"),
        types.InlineKeyboardButton(text="↩️ Отмена", callback_data=f"{PREFIX}:page:1"),
    )
    await cb.message.edit_text(
        f"Вы уверены, что хотите удалить код <code>{code}</code>? Доступ по нему будет закрыт.",
        reply_markup=kb.as_markup(),
        parse_mode="HTML",
    )
    await cb.answer()


@codes_router.callback_query(F.data.startswith(f"{PREFIX}:delete_confirm:"))
async def on_code_delete_confirm(cb: types.CallbackQuery):
    code = cb.data.split(":")[3]

    async with new_uow() as uow:
        try:
            link = await uow.share_links.get_by_code(code)
            if not link:
                ok = False
            else:
                ok = await uow.share_links.delete(link.id)
        except Exception:
            ok = False

    if not ok:
        await cb.answer("❌ Не удалось удалить код. Возможно, он уже был удалён.", show_alert=True)
        return

    await cb.answer("✅ Код удалён")
    await on_codes_root(cb)