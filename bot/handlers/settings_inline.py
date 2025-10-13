# bot/handlers/settings_inline.py
from __future__ import annotations

from typing import List, Tuple

from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import Schedule, Plant, User, ActionType
from bot.db_repo.schedules import SchedulesRepo

settings_router = Router(name="settings_inline")

PREFIX = "settings"
PAGE_SIZE = 7


# ---------- FSM ----------
class SettingsStates(StatesGroup):
    waiting_sub_code = State()


# ---------- Utils ----------
def _slice(items: list, page: int, size: int = PAGE_SIZE):
    total = len(items)
    pages = max(1, (total + size - 1) // size)
    page = max(1, min(page, pages))
    s, e = (page - 1) * size, (page - 1) * size + size
    return items[s:e], page, pages, total


def _weekly_mask_to_text(mask: int) -> str:
    days = ["Пн","Вт","Ср","Чт","Пт","Сб","Вс"]
    picked = [d for i, d in enumerate(days) if (mask >> i) & 1]
    return ",".join(picked) if picked else "—"


def _action_emoji(action: ActionType | str) -> str:
    val = action if isinstance(action, str) else action.value
    return {"watering": "💧", "fertilizing": "🧪", "repotting": "🪴", "custom": "🔖"}.get(val, "🔔")


async def create_user_by_tg(tg_id: int) -> User:
    async with new_uow() as uow:
        return await uow.users.create(tg_id)


# ---------- Public entry ----------
async def show_settings_menu(target: types.CallbackQuery | types.Message):
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="🔗 Поделиться расписанием", callback_data=f"{PREFIX}:share_wizard:start"))
    kb.row(types.InlineKeyboardButton(text="📬 Подписки", callback_data=f"{PREFIX}:subs"))
    kb.row(types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"))

    text = "⚙️ <b>Настройки</b>\nВыберите действие:"
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb.as_markup())
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb.as_markup())


# ---------- базовые ----------
@settings_router.callback_query(F.data == f"{PREFIX}:menu")
async def on_settings_menu(cb: types.CallbackQuery):
    await show_settings_menu(cb)

@settings_router.callback_query(F.data == f"{PREFIX}:noop")
async def on_noop(cb: types.CallbackQuery):
    await cb.answer()