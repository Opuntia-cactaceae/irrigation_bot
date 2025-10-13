# bot/handlers/calendar_subs_inline.py
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from typing import Optional

from bot.db_repo.models import ActionType
from bot.services.calendar_feed import get_feed_subs, Mode
from bot.services.cal_shared import (
    CODE_TO_ACTION as ACT_MAP,
    ACTION_TO_EMOJI as ACT_TO_EMOJI,
    ACTION_TO_CODE as ACT_TO_CODE,
)

calendar_subs_router = Router(name="calendar_subs_inline")

PREFIX = "cal_subs"
PAGE_SIZE_DAYS = 5


async def show_calendar_subs_root(
    target: types.Message | types.CallbackQuery,
    *,
    page: int = 1,
    mode: Mode = "upc",
    action: Optional[ActionType] = None,
):
    if isinstance(target, types.CallbackQuery):
        message = target.message
        user_id = target.from_user.id
    else:
        message = target
        user_id = target.from_user.id

    feed_page = await get_feed_subs(
        user_tg_id=user_id,
        action=action,
        mode=mode,
        page=page,
        days_per_page=PAGE_SIZE_DAYS,
    )

    header = _render_header(mode, action)
    body = _render_feed_text(feed_page)
    kb = _kb_calendar_subs(mode, feed_page.page, feed_page.pages, action)

    text = header + "\n" + body
    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await message.answer(text, reply_markup=kb)

def _render_header(mode: Mode, action: Optional[ActionType]) -> str:
    act_label = {
        None: "Все действия",
        ActionType.WATERING: "Полив",
        ActionType.FERTILIZING: "Подкормка",
        ActionType.REPOTTING: "Пересадка",
        ActionType.CUSTOM: "Другое",
    }.get(action, "Все действия")
    mode_label = "Ближайшие" if mode == "upc" else "История"
    return (
        f"📅 <b>Календарь подписок</b>\n"
        f"Фильтр: <b>{act_label}</b>\n"
        f"Раздел: <b>{mode_label}</b>"
    )


def _render_feed_text(feed_page) -> str:
    if not getattr(feed_page, "days", None):
        return "Пока событий нет."

    lines: list[str] = []
    for day in feed_page.days:
        d = day.date_local
        lines.append(f"\n📅 <b>{d:%d.%m (%a)}</b>")
        for it in day.items:
            emoji = ACT_TO_EMOJI.get(it.action, "•")
            t = it.dt_local.strftime("%H:%M")
            lines.append(f"  {t} {emoji} {it.plant_name} (id:{it.plant_id})")
    return "\n".join(lines).lstrip()


def _kb_calendar_subs(mode: Mode, page: int, pages: int, action: Optional[ActionType]):
    kb = InlineKeyboardBuilder()
    code_current = ACT_TO_CODE.get(action, "all")

    for text, code in (("💧", "w"), ("💊", "f"), ("🪴", "r"), ("👀", "all")):
        active = (code_current == code)
        mark = "✓ " if active and code != "all" else ""
        kb.button(
            text=f"{mark}{text}",
            callback_data=f"{PREFIX}:act:{mode}:{page}:{code}",
        )
    kb.adjust(4)

    kb.row(
        types.InlineKeyboardButton(
            text=("📌 Ближайшие ✓" if mode == "upc" else "📌 Ближайшие"),
            callback_data=f"{PREFIX}:feed:upc:1:{code_current}",
        ),
        types.InlineKeyboardButton(
            text=("📜 История ✓" if mode == "hist" else "📜 История"),
            callback_data=f"{PREFIX}:feed:hist:1:{code_current}",
        ),
    )

    has_prev = page > 1
    has_next = page < pages
    prev_page = page - 1 if has_prev else 1
    next_page = page + 1 if has_next else pages

    kb.row(
        types.InlineKeyboardButton(
            text="◀️" if has_prev else "⏺",
            callback_data=f"{PREFIX}:page:{mode}:{prev_page}:{code_current}",
        ),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(
            text="▶️" if has_next else "⏺",
            callback_data=f"{PREFIX}:page:{mode}:{next_page}:{code_current}",
        ),
    )
    kb.row(types.InlineKeyboardButton(text="↩️ Подписки", callback_data="settings:subs"))
    return kb.as_markup()


@calendar_subs_router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_calendar_subs_cb(cb: types.CallbackQuery):
    print("in calendar_subs_cb")
    parts = cb.data.split(":")
    cmd = parts[1] if len(parts) > 1 else "noop"
    if cmd == "noop":
        return await cb.answer()

    if cmd in ("feed", "page", "act"):
        mode: Mode = parts[2] if len(parts) > 2 else "upc"
        page = int(parts[3]) if len(parts) > 3 else 1
        act_code = parts[4] if len(parts) > 4 else "all"
        if not act_code or act_code == "None":
            act_code = "all"
        action = ACT_MAP.get(act_code)
        await show_calendar_subs_root(cb, page=page, mode=mode, action=action)
        return

    await cb.answer()

@calendar_subs_router.callback_query(F.data == "settings:subs_cal")
async def cal_entry_from_settings(cb: types.CallbackQuery):
    await show_calendar_subs_root(cb, page=1, mode="upc", action=None)
    await cb.answer()