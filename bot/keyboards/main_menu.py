# bot/keyboards/main_menu.py
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton


MENU_PREFIX = "menu"


CB_CALENDAR = f"{MENU_PREFIX}:calendar"
CB_PLANTS   = f"{MENU_PREFIX}:plants"
CB_DONE     = f"{MENU_PREFIX}:done"
CB_SETTINGS = f"{MENU_PREFIX}:settings"
CB_HELP     = f"{MENU_PREFIX}:help"

def main_menu_kb() -> InlineKeyboardMarkup:
    rows = [
        [
            InlineKeyboardButton(text="📅 Календарь", callback_data=CB_CALENDAR),
            InlineKeyboardButton(text="🌿 Растения",  callback_data=CB_PLANTS),
        ],
        [
            InlineKeyboardButton(text="✅ Отметить выполнение", callback_data=CB_DONE),
        ],
        [
            InlineKeyboardButton(text="⚙️ Настройки", callback_data=CB_SETTINGS),
            InlineKeyboardButton(text="❓ Помощь",    callback_data=CB_HELP),
        ]
    ]
    return InlineKeyboardMarkup(inline_keyboard=rows)