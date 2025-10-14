# bot/handlers/calendar_inline.py
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from aiogram.fsm.context import FSMContext
from datetime import timezone, datetime
from typing import Optional

from bot.handlers.schedule_inline import show_schedule_wizard
from bot.db_repo.models import ActionType
from bot.services.calendar_feed import get_feed, get_feed_subs, Mode
from bot.db_repo.unit_of_work import new_uow
from bot.services.cal_shared import CODE_TO_ACTION as ACT_MAP, ACTION_TO_EMOJI as ACT_TO_EMOJI, ACTION_TO_CODE as ACT_TO_CODE

calendar_router = Router(name="calendar_inline")

PREFIX = "cal"

PAGE_SIZE_DAYS = 5


async def show_calendar_root(
    target: types.Message | types.CallbackQuery,
    year: int,
    month: int,
    action: Optional[ActionType] = None,
    plant_id: Optional[int] = None,
    mode: Mode = "upc",
    page: int = 1,
    shared_only: bool = False,
):
    if isinstance(target, types.CallbackQuery):
        message = target.message
        user_id = target.from_user.id
    else:
        message = target
        user_id = target.from_user.id

    if shared_only:
        feed_page = await get_feed_subs(
            user_tg_id=user_id,
            action=action,
            mode=mode,
            page=page,
            days_per_page=PAGE_SIZE_DAYS,
        )
    else:
        feed_page = await get_feed(
            user_tg_id=user_id,
            action=action,
            plant_id=plant_id,
            mode=mode,
            page=page,
            days_per_page=PAGE_SIZE_DAYS,
        )

    header = _render_header(mode, action, plant_id, shared_only=shared_only)
    body = _render_feed_text(feed_page)
    kb = _kb_calendar(mode, feed_page.page, feed_page.pages, action, plant_id, shared_only=shared_only)

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
    shared_only: bool,
):
    kb = InlineKeyboardBuilder()

    for text, code in (("💧", "w"), ("💊", "f"), ("🪴", "r"), ("👀", "all")):
        active = (ACT_TO_CODE.get(action) == code)
        mark = "✓ " if active and code != "all" else ""
        kb.button(
            text=f"{mark}{text}",
            callback_data=f"{PREFIX}:act:{mode}:{page}:{code}:{plant_id or 0}:{int(shared_only)}",  # 👈 добавили флаг
        )
    kb.adjust(4)

    kb.row(
        types.InlineKeyboardButton(
            text=("👥 Только подписки ✓" if shared_only else "👥 Только подписки"),
            callback_data=f"{PREFIX}:shared_toggle:{mode}:{page}:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(not shared_only)}",
        )
    )

    upc_active = (mode == "upc")
    hist_active = (mode == "hist")
    kb.row(
        types.InlineKeyboardButton(
            text=("📌 Ближайшие ✓" if upc_active else "📌 Ближайшие"),
            callback_data=f"{PREFIX}:feed:upc:1:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}",
        ),
        types.InlineKeyboardButton(
            text=("📜 История ✓" if hist_active else "📜 История"),
            callback_data=f"{PREFIX}:feed:hist:1:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}",
        ),
    )

    has_prev = page > 1
    has_next = page < pages
    prev_page = page - 1 if has_prev else 1
    next_page = page + 1 if has_next else pages

    left_text = "◀️" if has_prev else "⏺"
    left_cb = (
        f"{PREFIX}:page:{mode}:{prev_page}:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}"
        if has_prev else f"{PREFIX}:noop"
    )
    right_text = "▶️" if has_next else "⏺"
    right_cb = (
        f"{PREFIX}:page:{mode}:{next_page}:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}"
        if has_next else f"{PREFIX}:noop"
    )

    kb.row(
        types.InlineKeyboardButton(text=left_text, callback_data=left_cb),
        types.InlineKeyboardButton(text=f"Стр. {page}/{pages}", callback_data=f"{PREFIX}:noop"),
        types.InlineKeyboardButton(text=right_text, callback_data=right_cb),
    )

    kb.row(
        types.InlineKeyboardButton(
            text="🗓️ Запланировать",
            callback_data=f"{PREFIX}:plan:{mode}:{page}:{ACT_TO_CODE.get(action)}:{plant_id or 0}:{int(shared_only)}",
        ),
        types.InlineKeyboardButton(
            text="🗑 Удалить расписания",
            callback_data="sdel:list:1",
        ),
    )
    kb.row(
        types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"),
    )

    return kb.as_markup()


def _render_header(mode: Mode, action: Optional[ActionType], plant_id: Optional[int], *, shared_only: bool) -> str:
    act_label = {
        None: "Все действия",
        ActionType.WATERING: "Полив",
        ActionType.FERTILIZING: "Удобрения",
        ActionType.REPOTTING: "Пересадка",
    }[action]
    mode_label = "Ближайшие" if mode == "upc" else "История"
    shared_lbl = " · 👥 Только из подписок" if shared_only else ""
    return (
        f"📅 <b>Календарь</b>\n"
        f"Фильтр: <b>{act_label}</b>{shared_lbl}\n"
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

    # общие параметры для всех команд этого хэндлера
    mode: Mode = (parts[2] if len(parts) > 2 else "upc")
    page = int(parts[3]) if len(parts) > 3 else 1
    act_code = parts[4] if len(parts) > 4 else "all"
    pid = int(parts[5]) if len(parts) > 5 else 0
    try:
        shared_only = bool(int(parts[6])) if len(parts) > 6 else False  # 👈 читаем флаг
    except Exception:
        shared_only = False

    # Историю здесь не рендерим — для неё отдельный роутер history_inline
    if mode == "hist" and cmd in ("root", "feed", "page", "act", "shared_toggle"):
        # Передаём управление истории (кнопки уже формируют корректный payload для history_inline)
        return

    if cmd in ("root", "feed", "page", "act", "shared_toggle"):
        action = ACT_MAP.get(act_code, None)
        plant_id = pid or None

        # если пришёл shared_toggle — уже получили новое значение в parts[6]
        return await show_calendar_root(
            cb,
            year=datetime.now().year,
            month=datetime.now().month,
            action=action,
            plant_id=plant_id,
            mode=mode,
            page=page,
            shared_only=shared_only,  # 👈 важное место
        )

    if cmd == "plan":
        return await show_schedule_wizard(cb, state)

    if cmd == "done":
        try:
            mode: Mode = parts[2]
            page = int(parts[3])
            act_code = parts[4]
            pid = int(parts[5])
            shared_only = bool(int(parts[6]))
            schedule_id = int(parts[7])
        except Exception:
            return await cb.answer("Не получилось отметить", show_alert=True)

        action = ACT_MAP.get(act_code, None)
        plant_id = pid or None

        async with new_uow() as uow:
            sch = await uow.schedules.get(schedule_id)
            if not sch or not getattr(sch, "active", True):
                await cb.answer("Расписание не найдено или отключено", show_alert=True)
                return await show_calendar_root(
                    cb,
                    datetime.now().year,
                    datetime.now().month,
                    action=action,
                    plant_id=plant_id,
                    mode=mode,
                    page=page,
                    shared_only=shared_only,
                )

            plant = await uow.plants.get(getattr(sch, "plant_id", None))
            if not plant:
                await cb.answer("Недоступно", show_alert=True)
                return

            me = await uow.users.get(cb.from_user.id)
            if getattr(plant, "user_id", None) != getattr(me, "id", None):
                await cb.answer("Недоступно", show_alert=True)
                return

            await uow.events.create(plant_id=sch.plant_id, action=sch.action, source="manual")

        from bot.scheduler import plan_next_for_schedule
        await plan_next_for_schedule(schedule_id)

        await cb.answer("Отмечено ✅", show_alert=False)
        return await show_calendar_root(
            cb,
            year=datetime.now().year,
            month=datetime.now().month,
            action=action,
            plant_id=plant_id,
            mode=mode,
            page=page,
            shared_only=shared_only,
        )

    await cb.answer()