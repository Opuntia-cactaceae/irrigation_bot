# bot/handlers/calendar_inline.py
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from datetime import timezone, datetime
from typing import Optional

from bot.handlers.schedule_inline import show_schedule_wizard
from bot.db_repo.models import ActionType
from bot.services.calendar_feed import get_feed, Mode
from bot.db_repo.unit_of_work import new_uow

calendar_router = Router(name="calendar_inline")

PREFIX = "cal"

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

PAGE_SIZE_DAYS = 5


async def show_calendar_root(
    target: types.Message | types.CallbackQuery,
    year: int,
    month: int,
    action: Optional[ActionType] = None,
    plant_id: Optional[int] = None,
    mode: Mode = "upc",
    page: int = 1,
):
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
        mode=mode,
        page=page,
        days_per_page=PAGE_SIZE_DAYS,
    )

    header = _render_header(mode, action, plant_id)
    body = _render_feed_text(feed_page)
    kb = _kb_calendar(mode, feed_page.page, feed_page.pages, action, plant_id)

    text = header + "\n" + body
    if isinstance(target, types.CallbackQuery):
        await message.edit_text(text, reply_markup=kb)
        await target.answer()
    else:
        await message.answer(text, reply_markup=kb)


def _kb_calendar(
    mode: Mode,
    page: int,
    pages: int,
    action: Optional[ActionType],
    plant_id: Optional[int],
):
    kb = InlineKeyboardBuilder()

    for text, code in (("💧", "w"), ("💊", "f"), ("🪴", "r"), ("👀", "all")):
        active = (ACT_TO_CODE.get(action) == code)
        mark = "✓ " if active and code != "all" else ""
        kb.button(
            text=f"{mark}{text}",
            callback_data=f"{PREFIX}:act:{mode}:{page}:{code}:{plant_id or 0}",
        )
    kb.adjust(4)

    upc_active = (mode == "upc")
    hist_active = (mode == "hist")
    kb.row(
        types.InlineKeyboardButton(
            text=("📌 Ближайшие ✓" if upc_active else "📌 Ближайшие"),
            callback_data=f"{PREFIX}:feed:upc:1:{ACT_TO_CODE.get(action)}:{plant_id or 0}",
        ),
        types.InlineKeyboardButton(
            text=("📜 История ✓" if hist_active else "📜 История"),
            callback_data=f"{PREFIX}:feed:hist:1:{ACT_TO_CODE.get(action)}:{plant_id or 0}",
        ),
    )

    kb.row(
        types.InlineKeyboardButton(
            text="◀️",
            callback_data=f"{PREFIX}:page:{mode}:{max(1, page-1)}:{ACT_TO_CODE.get(action)}:{plant_id or 0}",
        ),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(
            text="▶️",
            callback_data=f"{PREFIX}:page:{mode}:{min(pages, page+1)}:{ACT_TO_CODE.get(action)}:{plant_id or 0}",
        ),
    )

    kb.row(
        types.InlineKeyboardButton(
            text="🗓️ Запланировать",
            callback_data=f"{PREFIX}:plan:{mode}:{page}:{ACT_TO_CODE.get(action)}:{plant_id or 0}",
        ),
    )
    kb.row(
        types.InlineKeyboardButton(text="🌿 Растения", callback_data="plants:page:1:0"),
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )

    return kb.as_markup()


def _render_header(mode: Mode, action: Optional[ActionType], plant_id: Optional[int]) -> str:
    act_label = {
        None: "Все действия",
        ActionType.WATERING: "Полив",
        ActionType.FERTILIZING: "Удобрения",
        ActionType.REPOTTING: "Пересадка",
    }[action]
    mode_label = "Ближайшие" if mode == "upc" else "История"
    plant_label = "Все растения" if not plant_id else f"Растение #{plant_id}"
    return (
        f"📅 <b>Календарь</b>\n"
        f"Фильтр: <b>{act_label}</b> · <i>{plant_label}</i>\n"
        f"Раздел: <b>{mode_label}</b>"
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


@calendar_router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_calendar_callbacks(cb: types.CallbackQuery, state: FSMContext):
    parts = cb.data.split(":")
    cmd = parts[1] if len(parts) > 1 else "noop"

    if cmd == "noop":
        return await cb.answer()

    if cmd in ("root", "feed", "page", "act"):
        mode: Mode = (parts[2] if len(parts) > 2 else "upc")
        page = int(parts[3]) if len(parts) > 3 else 1
        act_code = parts[4] if len(parts) > 4 else "all"
        pid = int(parts[5]) if len(parts) > 5 else 0

        action = ACT_MAP.get(act_code, None)
        plant_id = pid or None

        return await show_calendar_root(
            cb,
            year=datetime.now().year,
            month=datetime.now().month,
            action=action,
            plant_id=plant_id,
            mode=mode,
            page=page,
        )

    if cmd == "plan":
        return await show_schedule_wizard(cb, state)

    if cmd == "done":
        try:
            mode: Mode = parts[2]
            page = int(parts[3])
            act_code = parts[4]
            pid = int(parts[5])
            schedule_id = int(parts[6])
        except Exception:
            return await cb.answer("Не получилось отметить", show_alert=True)

        action = ACT_MAP.get(act_code, None)
        plant_id = pid or None

        async with new_uow() as uow:
            sch = await uow.schedules.get(schedule_id)
            if not sch or not getattr(sch, "active", True):
                await cb.answer("Расписание не найдено или отключено", show_alert=True)
                return await show_calendar_root(cb, datetime.now().year, datetime.now().month, action=action, plant_id=plant_id, mode=mode, page=page)

            plant = await uow.plants.get(getattr(sch, "plant_id", None))
            if not plant:
                await cb.answer("Недоступно", show_alert=True)
                return

            me = await uow.users.get_or_create(cb.from_user.id)
            if getattr(plant, "user_id", None) != getattr(me, "id", None):
                await cb.answer("Недоступно", show_alert=True)
                return

            await uow.events.create(plant_id=sch.plant_id, action=sch.action, source="manual")

        from bot.scheduler import plan_next_for_schedule
        await plan_next_for_schedule(cb.bot, schedule_id)

        await cb.answer("Отмечено ✅", show_alert=False)
        return await show_calendar_root(
            cb,
            year=datetime.now().year,
            month=datetime.now().month,
            action=action,
            plant_id=plant_id,
            mode=mode,
            page=page,
        )

    await cb.answer()