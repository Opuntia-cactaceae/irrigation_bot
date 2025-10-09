# bot/handlers/main_menu.py
from aiogram import Router, types, F
from aiogram.filters import CommandStart
from datetime import datetime, timezone

from bot.keyboards.main_menu import (
    main_menu_kb,
    MENU_PREFIX,
    CB_CALENDAR, CB_PLANTS, CB_DONE, CB_SETTINGS, CB_HELP,
)

main_menu_router = Router(name="main_menu")

MENU_TITLE = "🤖 Помощник по уходу за растениями"

async def show_main_menu(message_or_cb: types.Message | types.CallbackQuery):
    text = (
        f"{MENU_TITLE}\n\n"
        "Выберите раздел:\n"
        "• 📅 Календарь — посмотреть план по датам\n"
        "• 🌿 Растения — список и управление\n"
        "• ✅ Отметить выполнено — быстро зафиксировать действие\n"
        "• ⚙️ Настройки — часовой пояс и пр."
    )
    kb = main_menu_kb()

    if isinstance(message_or_cb, types.CallbackQuery):
        await message_or_cb.message.edit_text(text, reply_markup=kb)
        await message_or_cb.answer()
    else:
        await message_or_cb.answer(text, reply_markup=kb)

async def on_start(m: types.Message):
    await show_main_menu(m)

# --- обработчик нажатий главного меню ---
@main_menu_router.callback_query(F.data.startswith(MENU_PREFIX + ":"))
async def on_main_menu_click(cb: types.CallbackQuery):
    data = cb.data

    if data == CB_CALENDAR:
        from .calendar_inline import show_calendar_root
        now = datetime.now(timezone.utc)
        await show_calendar_root(cb, year=now.year, month=now.month, action=None, plant_id=None)
        return

    if data == CB_PLANTS:
        from .plants_inline import show_plants_list
        await show_plants_list(cb, page=1)
        return

    if data == CB_DONE:
        from .quick_done_inline import show_quick_done_menu
        print("CB_DONE")
        await show_quick_done_menu(cb)
        return

    if data == CB_SETTINGS:
        from .settings_inline import show_settings_menu
        await show_settings_menu(cb)
        return

    if data == CB_HELP:
        from .help_inline import show_help
        await show_help(cb)
        return

    await show_main_menu(cb)