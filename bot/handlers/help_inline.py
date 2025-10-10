# bot/handlers/help_inline.py
from __future__ import annotations

from aiogram import Router, F, types
from aiogram.filters import Command
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.keyboards.main_menu import MENU_PREFIX

help_router = Router(name="help_inline")

PREFIX = "help"
CB_MENU_ROOT = f"{MENU_PREFIX}:root"


# ---------- keyboards ----------
def kb_help_root() -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="📖 FAQ", callback_data=f"{PREFIX}:faq")
    kb.button(text="ℹ️ О проекте", callback_data=f"{PREFIX}:about")
    kb.button(text="↩️ В меню", callback_data=CB_MENU_ROOT)
    kb.adjust(1)
    return kb.as_markup()


def kb_help_back() -> types.InlineKeyboardMarkup:
    kb = InlineKeyboardBuilder()
    kb.button(text="↩️ Назад", callback_data=f"{PREFIX}:root")
    return kb.as_markup()


# ---------- main content ----------
HELP_ROOT_TEXT = (
    "❓ <b>Помощь</b>\n\n"
    "💧 Настройте расписания для полива, удобрений и пересадки.\n"
    "📅 В календаре отображаются ближайшие даты.\n"
    "✅ Через кнопку «Отметить выполнение» фиксируйте действия — "
    "и бот пересчитает следующую дату автоматически.\n\n"
    "Дополнительно можно:\n"
    "• Поделиться расписанием с другими пользователями\n"
    "• Подписаться на чужие расписания\n\n"
    "Выберите раздел ниже или вернитесь в меню ⬇️"
)

HELP_FAQ_TEXT = (
    "📖 <b>FAQ</b>\n\n"
    "• <b>Что делать, если не приходят уведомления?</b>\n"
    "  Проверьте, что бот не в муте и расписание активно.\n\n"
    "• <b>Как изменить частоту напоминаний?</b>\n"
    "  Откройте расписание → «Редактировать» → выберите интервал.\n\n"
    "• <b>Можно ли делиться расписанием?</b>\n"
    "  Да, через кнопку «Поделиться» — другой пользователь сможет подписаться.\n"
)

HELP_ABOUT_TEXT = (
    "ℹ️ <b>О проекте</b>\n\n"
    "Этот бот помогает автоматизировать уход за растениями 🌱.\n"
    "Создавайте расписания, получайте напоминания и отмечайте выполненные задачи.\n\n"
    "Козара (aka Косара) —️ Контроль За Растениями"
)


# ---------- public API ----------
async def show_help(target: types.Message | types.CallbackQuery):
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(HELP_ROOT_TEXT, reply_markup=kb_help_root())
        return await target.answer()
    else:
        return await target.answer(HELP_ROOT_TEXT, reply_markup=kb_help_root())


# ---------- callbacks ----------
@help_router.callback_query(F.data.startswith(f"{PREFIX}:"))
async def on_help_callbacks(cb: types.CallbackQuery):
    parts = cb.data.split(":")
    action = parts[1] if len(parts) > 1 else "root"

    if action == "root":
        await cb.message.edit_text(HELP_ROOT_TEXT, reply_markup=kb_help_root())
    elif action == "faq":
        await cb.message.edit_text(HELP_FAQ_TEXT, reply_markup=kb_help_back())
    elif action == "about":
        await cb.message.edit_text(HELP_ABOUT_TEXT, reply_markup=kb_help_back())
    else:
        await cb.answer()  # fallback


# ---------- command /help ----------
@help_router.message(Command("help"))
async def cmd_help(msg: types.Message):
    await msg.answer(HELP_ROOT_TEXT, reply_markup=kb_help_root())