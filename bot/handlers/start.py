# bot/handlers/start.py
from aiogram import Router, types
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.keyboards.main_menu import MENU_PREFIX

from bot.handlers.timezone import show_timezone_prompt  # <- NEW

router = Router(name="start")


@router.message(CommandStart())
async def start(m: types.Message, state):
    tg_id = m.from_user.id
    tg_username = m.from_user.username

    need_tz_setup = False

    async with new_uow() as uow:
        user = await uow.users.get(tg_id)

        if not user:
            user = await uow.users.create(id=tg_id, tz=None, tg_username=tg_username)
            need_tz_setup = True

        await uow.commit()

    if need_tz_setup:
        await show_timezone_prompt(m, state)
        return

    kb = InlineKeyboardBuilder()
    kb.row(
        types.InlineKeyboardButton(
            text="📋 Открыть главное меню",
            callback_data=f"{MENU_PREFIX}:root"
        )
    )

    await m.answer(
        "Привет! Я помогу с поливом 🌿\n"
        "Нажми кнопку ниже, чтобы открыть главное меню.",
        reply_markup=kb.as_markup()
    )