# bot/handlers/start.py
from aiogram import Router, types
from aiogram.filters import CommandStart

from bot.db_repo.unit_of_work import new_uow

router = Router(name="start")


@router.message(CommandStart())
async def start(m: types.Message):
    tg_id = m.from_user.id

    # создаём пользователя при первом старте (или получаем существующего)
    async with new_uow() as uow:
        user = await uow.users.get_or_create(tg_id)

        # при желании можно выставить TZ по умолчанию один раз
        # (если в модели поле tz nullable и не задано)
        if getattr(user, "tz", None) in (None, ""):
            try:
                user.tz = "UTC"  # или, если хочешь, "Europe/Amsterdam"
                await uow.session.flush()  # чтобы сохранить изменение вместе с коммитом контекста
            except Exception:
                pass

    await m.answer(
        "Привет! Я помогу с поливом 🌿\n"
        "Добавь растение: /add_plant\n"
        "Открой календарь: /calendar"
    )