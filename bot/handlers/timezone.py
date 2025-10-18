# bot/handlers/timezone.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Optional, List
import re
from zoneinfo import ZoneInfo, available_timezones

from aiogram import Router, types, F
from aiogram.filters import Command
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import StatesGroup, State
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow

try:
    from .settings_inline import show_settings_menu  # опционально
except Exception:
    show_settings_menu = None  # если нет — проигнорируем

timezone_router = Router(name="timezone")

class TimezoneState(StatesGroup):
    waiting_input = State()
    browsing = State()


TZ_PREFIX = "tz"
CB_TZ_SET = f"{TZ_PREFIX}:set"
CB_TZ_MORE = f"{TZ_PREFIX}:more"


def _is_candidate_zone(name: str) -> bool:
    if name.startswith(("Etc/", "posix/", "right/")):
        return False
    return name.count("/") >= 1

def infer_timezones_by_local(
    *,
    user_day: int,
    user_hour: int,
    user_minute: Optional[int] = None,
    user_full_date: Optional[datetime] = None,
    now_utc: Optional[datetime] = None,
) -> List[str]:
    if now_utc is None:
        now_utc = datetime.now(timezone.utc)

    zones = []
    for name in available_timezones():
        if not _is_candidate_zone(name):
            continue
        try:
            local_now = now_utc.astimezone(ZoneInfo(name))
        except Exception:
            continue

        if user_full_date is not None:
            if local_now.date() != user_full_date.date():
                continue
        else:
            if local_now.day != user_day:
                continue

        if local_now.hour != user_hour:
            continue
        if user_minute is not None and local_now.minute != user_minute:
            continue

        zones.append(name)

    preferred_order = ("Europe", "America", "Asia", "Africa", "Australia", "Pacific", "Atlantic", "Indian", "Antarctica")
    zones.sort(key=lambda z: (preferred_order.index(z.split("/")[0]) if z.split("/")[0] in preferred_order else 999, z))
    return zones

def _parse_user_input(text: str):
    text = text.strip()
    # YYYY-MM-DD HH:MM
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2}):(\d{2})", text)
    if m:
        y, mo, d, h, mi = map(int, m.groups())
        return {"full_dt": datetime(y, mo, d, h, mi), "day": d, "hour": h, "minute": mi}

    # YYYY-MM-DD HH
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})\s+(\d{1,2})", text)
    if m:
        y, mo, d, h = map(int, m.groups())
        return {"full_dt": datetime(y, mo, d, h, 0), "day": d, "hour": h, "minute": None}

    # DD HH
    m = re.fullmatch(r"(\d{1,2})\s+(\d{1,2})", text)
    if m:
        d, h = map(int, m.groups())
        return {"full_dt": None, "day": d, "hour": h, "minute": None}

    return None

async def _render_tz_page(msg: types.Message, candidates: list[str], page: int, per_page: int = 12):
    start = page * per_page
    chunk = candidates[start:start + per_page]

    kb = InlineKeyboardBuilder()
    for tz in chunk:
        kb.row(types.InlineKeyboardButton(text=tz, callback_data=f"{CB_TZ_SET}:{tz}"))
    if start + per_page < len(candidates):
        kb.row(types.InlineKeyboardButton(text="Показать ещё", callback_data=f"{CB_TZ_MORE}:{page+1}"))

    text = f"Нашёл {len(candidates)} вариантов. Выбери свою таймзону:"
    if msg.edit_date:
        await msg.edit_text(text, reply_markup=kb.as_markup())
    else:
        await msg.answer(text, reply_markup=kb.as_markup())

# ------- публичные entrypoints --------
async def show_timezone_prompt(message_or_cb: types.Message | types.CallbackQuery, state: FSMContext):
    text = (
        "Введи *текущую дату и час* у тебя.\n\n"
        "Минимально: `DD HH` (например, `15 9`).\n"
        "Точнее: `YYYY-MM-DD HH` или `YYYY-MM-DD HH:MM` (например, `2025-10-15 09:00`)."
    )
    if isinstance(message_or_cb, types.CallbackQuery):
        await message_or_cb.message.edit_text(text, parse_mode="Markdown")
        await message_or_cb.answer()
    else:
        await message_or_cb.answer(text, parse_mode="Markdown")
    await state.set_state(TimezoneState.waiting_input)


@timezone_router.message(Command("timezone"))
async def on_timezone_command(m: types.Message, state: FSMContext):
    await show_timezone_prompt(m, state)


@timezone_router.message(TimezoneState.waiting_input, F.text)
async def on_timezone_input(m: types.Message, state: FSMContext):
    parsed = _parse_user_input(m.text or "")
    if not parsed:
        await m.answer("Неверный формат. Введи `DD HH` или `YYYY-MM-DD HH[:MM]`.")
        return

    candidates = infer_timezones_by_local(
        user_day=parsed["day"],
        user_hour=parsed["hour"],
        user_minute=parsed["minute"],
        user_full_date=parsed["full_dt"],
    )

    if not candidates:
        await m.answer("Не нашёл подходящих таймзон. Проверь дату/час и попробуй ещё раз.")
        return

    if len(candidates) == 1:
        tz_name = candidates[0]
        async with new_uow() as uow:

            await uow.users.set_timezone(m.from_user.id, tz_name)

            if m.from_user.username:
                user = await uow.users.get(m.from_user.id)
                if user:
                    user.tg_username = m.from_user.username
                    await uow.session.flush()
            await uow.commit()

        await m.answer(f"Таймзона установлена: *{tz_name}*", parse_mode="Markdown")

        if show_settings_menu:
            await show_settings_menu(m)
        await state.clear()
        return

    await state.update_data(tz_candidates=candidates, page=0)
    await _render_tz_page(m, candidates, page=0)
    await state.set_state(TimezoneState.browsing)

@timezone_router.callback_query(TimezoneState.browsing, F.data.startswith(CB_TZ_MORE + ":"))
async def on_tz_more(cb: types.CallbackQuery, state: FSMContext):
    _, page_str = cb.data.split(":", 1)
    page = int(page_str)

    data = await state.get_data()
    candidates: list[str] = data.get("tz_candidates") or []
    if not candidates:
        await cb.answer("Данные устарели. Введи время ещё раз")
        await state.set_state(TimezoneState.waiting_input)
        return

    await cb.answer()
    await state.update_data(page=page)
    await _render_tz_page(cb.message, candidates, page)

@timezone_router.callback_query(TimezoneState.browsing, F.data.startswith(CB_TZ_SET + ":"))
async def on_tz_set(cb: types.CallbackQuery, state: FSMContext):
    _, tz_name = cb.data.split(":", 1)

    async with new_uow() as uow:
        await uow.users.set_timezone(cb.from_user.id, tz_name)
        if cb.from_user.username:
            user = await uow.users.get(cb.from_user.id)
            if user:
                user.tg_username = cb.from_user.username
                await uow.session.flush()
        await uow.commit()

    await cb.answer("Сохранено ✅")

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="🔙 В меню таймзон", callback_data=f"{TZ_PREFIX}:main"))

    try:
        await cb.message.edit_text(
            f"Таймзона установлена: *{tz_name}*",
            parse_mode="Markdown",
            reply_markup=kb.as_markup(),
        )
    except Exception:
        await cb.message.answer(
            f"Таймзона установлена: *{tz_name}*",
            parse_mode="Markdown",
            reply_markup=kb.as_markup(),
        )

    if show_settings_menu:
        await show_settings_menu(cb)

    await state.clear()