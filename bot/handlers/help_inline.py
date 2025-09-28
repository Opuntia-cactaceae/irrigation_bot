# bot/handlers/help_inline.py
from __future__ import annotations

from aiogram import Router, types, F
from aiogram.utils.keyboard import InlineKeyboardBuilder
from bot.keyboards.main_menu import MENU_PREFIX

help_router = Router(name="help_inline")

PREFIX = "help"
CB_MENU_ROOT = f"{MENU_PREFIX}:root"


# ---------- keyboards ----------
def kb_help_root():
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="↩️ Меню", callback_data=CB_MENU_ROOT))
    return kb.as_markup()


# ---------- public API ----------
async def show_help(target: types.Message | types.CallbackQuery):
    text = (
        "❓ <b>Помощь</b>\n\n"
        "• Настройте расписания для полива/удобрений/пересадки.\n"
        "• В календаре видны ближайшие даты.\n"
        "• Через «Отметить выполнено» фиксируйте действия, и мы пересчитаем следующий раз.\n\n"
        "↩️ Вернуться в меню — кнопка ниже."
    )

    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb_help_root())
        return await target.answer()
    else:
        return await target.answer(text, reply_markup=kb_help_root())


# ---------- callbacks ----------
@help_router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_help_callbacks(cb: types.CallbackQuery):
    """
    На будущее: если появятся подпункты 'help:faq', 'help:about' и т.д.,
    обрабатывай их здесь. Пока оставим noop и fallback.
    """
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "noop"

    if action == "noop":
        return await cb.answer()

    # fallback
    await cb.answer()