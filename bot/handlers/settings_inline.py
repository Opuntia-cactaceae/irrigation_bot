# bot/handlers/settings_inline.py
from __future__ import annotations

from typing import List, Tuple
from datetime import datetime
from zoneinfo import ZoneInfo

from aiogram import Router, types, F
from aiogram.fsm.state import StatesGroup, State
from aiogram.fsm.context import FSMContext
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import Schedule, Plant, User, ActionType
from bot.db_repo.schedules import SchedulesRepo

from bot.handlers.timezone import show_timezone_prompt

settings_router = Router(name="settings_inline")

PREFIX = "settings"
PAGE_SIZE = 7


class SettingsStates(StatesGroup):
    waiting_sub_code = State()
    waiting_new_nick = State()


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


async def show_settings_menu(target: types.CallbackQuery | types.Message):
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="👤 Пользователь", callback_data=f"{PREFIX}:user"))  # <- NEW
    kb.row(types.InlineKeyboardButton(text="🔗 Поделиться расписанием", callback_data=f"{PREFIX}:share_wizard:start"))
    kb.row(types.InlineKeyboardButton(text="📬 Подписки", callback_data=f"{PREFIX}:subs"))
    kb.row(types.InlineKeyboardButton(text="↩️ Меню", callback_data="menu:root"))

    text = "⚙️ <b>Настройки</b>\nВыберите действие:"
    if isinstance(target, types.CallbackQuery):
        await target.message.edit_text(text, reply_markup=kb.as_markup())
        await target.answer()
    else:
        await target.answer(text, reply_markup=kb.as_markup())


@settings_router.callback_query(F.data == f"{PREFIX}:menu")
async def on_settings_menu(cb: types.CallbackQuery):
    await show_settings_menu(cb)

@settings_router.callback_query(F.data == f"{PREFIX}:noop")
async def on_noop(cb: types.CallbackQuery):
    await cb.answer()

@settings_router.callback_query(F.data == f"{PREFIX}:user")
async def on_user_root(cb: types.CallbackQuery):
    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="🕒 Таймзона", callback_data=f"{PREFIX}:user:tz"))
    kb.row(types.InlineKeyboardButton(text="📝 Ник", callback_data=f"{PREFIX}:user:nick"))
    kb.row(types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{PREFIX}:menu"))
    await cb.message.edit_text("👤 <b>Пользователь</b>\nВыберите раздел:", reply_markup=kb.as_markup())
    await cb.answer()


@settings_router.callback_query(F.data == f"{PREFIX}:user:tz")
async def on_user_timezone(cb: types.CallbackQuery):
    async with new_uow() as uow:
        user = await uow.users.get(cb.from_user.id)

    tz_name = getattr(user, "tz", None) or "UTC"
    try:
        now_local = datetime.now(ZoneInfo(tz_name))
    except Exception:
        tz_name = "UTC"
        now_local = datetime.now(ZoneInfo("UTC"))

    text = (
        "🕒 <b>Таймзона</b>\n"
        f"Текущая таймзона: <code>{tz_name}</code>\n"
        f"Сейчас там: <code>{now_local:%Y-%m-%d %H:%M}</code>"
    )

    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(text="🔁 Сменить", callback_data=f"{PREFIX}:user:tz:change"),
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{PREFIX}:user")
    )
    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()


@settings_router.callback_query(F.data == f"{PREFIX}:user:tz:change")
async def on_user_timezone_change(cb: types.CallbackQuery, state: FSMContext):
    if show_timezone_prompt:
        await show_timezone_prompt(cb, state)
    else:
        await cb.answer("Не удалось запустить смену таймзоны", show_alert=True)

@settings_router.callback_query(F.data == f"{PREFIX}:user:nick")
async def on_user_nick(cb: types.CallbackQuery):
    async with new_uow() as uow:
        user = await uow.users.get(cb.from_user.id)

    stored_nick = getattr(user, "tg_username", None) or "—"
    tg_username = cb.from_user.username or "—"

    text = (
        "📝 <b>Ник</b>\n"
        f"Сохранённый ник в боте: <code>{stored_nick}</code>\n"
        f"Ваш Telegram: <code>{tg_username}</code>\n\n"
        "Можно хранить свой ник в боте — он не обязан совпадать с Telegram."
    )

    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(text="🔁 Сменить", callback_data=f"{PREFIX}:user:nick:change"),
        types.InlineKeyboardButton(text="⬅️ Назад", callback_data=f"{PREFIX}:user")
    )
    await cb.message.edit_text(text, reply_markup=kb.as_markup())
    await cb.answer()


@settings_router.callback_query(F.data == f"{PREFIX}:user:nick:change")
async def on_user_nick_change(cb: types.CallbackQuery, state: FSMContext):
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="🚫 Отмена", callback_data=f"{PREFIX}:user:nick:cancel"))
    prompt = await cb.message.answer(
        "Введите новый ник (1–32 символа).",
        reply_markup=kb.as_markup()
    )

    await state.set_state(SettingsStates.waiting_new_nick)
    await state.update_data(
        nick_prompt_chat_id=prompt.chat.id,
        nick_prompt_message_id=prompt.message_id,
    )

    await cb.answer()


@settings_router.callback_query(F.data == f"{PREFIX}:user:nick:cancel")
async def on_user_nick_cancel(cb: types.CallbackQuery, state: FSMContext):
    try:
        await cb.message.edit_reply_markup(reply_markup=None)
    except Exception:
        pass

    await state.clear()
    await on_user_nick(cb)
    await cb.answer("Отменено")


@settings_router.message(SettingsStates.waiting_new_nick, F.text)
async def on_user_nick_input(m: types.Message, state: FSMContext):
    raw = (m.text or "").strip()

    if not (1 <= len(raw) <= 32):
        await m.answer("Ник должен быть длиной от 1 до 32 символов. Попробуйте ещё раз или нажмите «Отмена».")
        return

    async with new_uow() as uow:
        await uow.users.set_username(m.from_user.id, raw)
        await uow.commit()

    data = await state.get_data()
    prompt_chat_id = data.get("nick_prompt_chat_id")
    prompt_message_id = data.get("nick_prompt_message_id")
    if prompt_chat_id and prompt_message_id:
        try:
            await m.bot.edit_message_reply_markup(
                chat_id=prompt_chat_id,
                message_id=prompt_message_id,
                reply_markup=None
            )
        except Exception:
            pass

    await state.clear()

    kb = InlineKeyboardBuilder()
    kb.row(types.InlineKeyboardButton(text="⚙️ Настройки", callback_data=f"{PREFIX}:menu"))
    await m.answer(f"Готово! Ник обновлён: <b>{raw}</b>", parse_mode="HTML", reply_markup=kb.as_markup())