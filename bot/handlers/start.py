# bot/handlers/start.py
from aiogram import Router, types
from aiogram.filters import CommandStart
from aiogram.utils.keyboard import InlineKeyboardBuilder

from bot.db_repo.unit_of_work import new_uow
from bot.keyboards.main_menu import MENU_PREFIX


router = Router(name="start")


@router.message(CommandStart())
async def start(m: types.Message):
    tg_id = m.from_user.id

    async with new_uow() as uow:

        user = await uow.users.get(tg_id)
        if not user:
            user = await uow.users.create(tg_user_id=tg_id, tz="UTC")

        if not getattr(user, "tz", None):
            await uow.users.set_timezone(user.id, "UTC")

        await uow.commit()

        kb = InlineKeyboardBuilder()
        kb.row(
            types.InlineKeyboardButton(
                text="📋 Открыть главное меню",
                callback_data=f"{MENU_PREFIX}:root"  # попадает в on_main_menu_click и покажет меню
            )
        )

        await m.answer(
            "Привет! Я помогу с поливом 🌿\n"
            "Нажми кнопку ниже, чтобы открыть главное меню.",
            reply_markup=kb.as_markup()
        )