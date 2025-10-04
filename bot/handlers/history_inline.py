# bot/handlers/history_inline.py
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from datetime import datetime, timezone
from typing import Optional

from bot.services.calendar_feed import get_feed, Mode
from bot.db_repo.models import ActionType

history_router = Router(name="history_inline")

PREFIX = "cal"  # тот же префикс, что и в календаре
PAGE_SIZE_DAYS = 5

ACT_MAP: dict[str, Optional[ActionType]] = {
    "all": None,
    "w": ActionType.WATERING,
    "f": ActionType.FERTILIZING,
    "r": ActionType.REPOTTING,
}
ACT_TO_EMOJI = {
    ActionType.WATERING: "💧",
    ActionType.FERTILIZING: "💊",
    ActionType.REPOTTING: "🪴",
}
ACT_TO_CODE: dict[Optional[ActionType], str] = {
    None: "all",
    ActionType.WATERING: "w",
    ActionType.FERTILIZING: "f",
    ActionType.REPOTTING: "r",
}


async def show_history_root(
    target: types.Message | types.CallbackQuery,
    *,
    action: Optional[ActionType] = None,
    plant_id: Optional[int] = None,
    page: int = 1,
):
    # получаем message и user_id из target
    if isinstance(target, types.CallbackQuery):
        message = target.message
        user_id = target.from_user.id
    else:
        message = target
        user_id = target.from_user.id

    feed_page = await get_feed(
        user_tg_id=user_id,
        action=action,
        plant_id=plant_id,
        mode="hist",
        page=page,
        days_per_page=PAGE_SIZE_DAYS,
    )

    header = _render_header(action, plant_id)
    body = _render_feed_text(feed_page)
    kb = _kb_history(page=feed_page.page, pages=feed_page.pages, action=action, plant_id=plant_id)

    text = header + "\n" + body
    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await message.answer(text, reply_markup=kb)


def _kb_history(page: int, pages: int, action: Optional[ActionType], plant_id: Optional[int]):
    """Клавиатура для истории (mode=hist)."""
    kb = InlineKeyboardBuilder()

    # переключатели по действию
    for text, code in (("💧", "w"), ("💊", "f"), ("🪴", "r"), ("👀", "all")):
        active = (ACT_TO_CODE.get(action) == code)
        mark = "✓ " if active and code != "all" else ""
        kb.button(
            text=f"{mark}{text}",
            callback_data=f"{PREFIX}:act:hist:{page}:{code}:{plant_id or 0}",
        )
    kb.adjust(4)

    # переключение между разделами
    kb.row(
        types.InlineKeyboardButton(
            text="📌 Ближайшие",
            callback_data=f"{PREFIX}:feed:upc:1:{ACT_TO_CODE.get(action)}:{plant_id or 0}",
        ),
        types.InlineKeyboardButton(
            text="📜 История ✓",
            callback_data=f"{PREFIX}:feed:hist:1:{ACT_TO_CODE.get(action)}:{plant_id or 0}",
        ),
    )

    # пагинация
    kb.row(
        types.InlineKeyboardButton(
            text="◀️",
            callback_data=f"{PREFIX}:page:hist:{max(1, page-1)}:{ACT_TO_CODE.get(action)}:{plant_id or 0}",
        ),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(
            text="▶️",
            callback_data=f"{PREFIX}:page:hist:{min(pages, page+1)}:{ACT_TO_CODE.get(action)}:{plant_id or 0}",
        ),
    )

    # нижний ряд навигации
    kb.row(
        types.InlineKeyboardButton(text="🌿 Растения", callback_data="plants:page:1:0"),
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )
    return kb.as_markup()


def _render_header(action: Optional[ActionType], plant_id: Optional[int]) -> str:
    act_label = {
        None: "Все действия",
        ActionType.WATERING: "Полив",
        ActionType.FERTILIZING: "Удобрения",
        ActionType.REPOTTING: "Пересадка",
    }[action]
    plant_label = "Все растения" if not plant_id else f"Растение #{plant_id}"
    return (
        f"📅 <b>Календарь</b>\n"
        f"Фильтр: <b>{act_label}</b> · <i>{plant_label}</i>\n"
        f"Раздел: <b>История</b>"
    )


def _render_feed_text(feed_page) -> str:
    if not getattr(feed_page, "days", None):
        return "Пока пусто."

    lines: list[str] = []
    for day in feed_page.days:
        d = day.date_local
        lines.append(f"\n📅 <b>{d:%d.%m (%a)}</b>")
        for it in day.items:
            emoji = ACT_TO_EMOJI.get(it.action, "•")
            if hasattr(it, "dt_local") and it.dt_local:
                t = it.dt_local.strftime("%H:%M")
            else:
                t = it.dt_utc.astimezone(timezone.utc).strftime("%H:%M")
            lines.append(f"  {t} {emoji} {it.plant_name} (id:{it.plant_id})")
    return "\n".join(lines).lstrip()


@history_router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_history_callbacks(cb: types.CallbackQuery):
    """
    Обрабатываем только ветку с mode='hist':
      cal:feed:hist:...
      cal:page:hist:...
      cal:act:hist:...
    Остальное игнорируем — оставим calendar_inline.
    """
    parts = cb.data.split(":")
    cmd = parts[1] if len(parts) > 1 else "noop"

    # быстро выходим, если это не история
    if cmd not in ("feed", "page", "act", "root"):
        return
    mode: Mode = (parts[2] if len(parts) > 2 else "upc")
    if mode != "hist":
        return

    if cmd in ("root", "feed", "page", "act"):
        page = int(parts[3]) if len(parts) > 3 else 1
        act_code = parts[4] if len(parts) > 4 else "all"
        pid = int(parts[5]) if len(parts) > 5 else 0

        action = ACT_MAP.get(act_code, None)
        plant_id = pid or None

        return await show_history_root(
            cb,
            action=action,
            plant_id=plant_id,
            page=page,
        )

    await cb.answer()