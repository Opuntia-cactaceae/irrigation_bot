# bot/handlers/plants.py
from aiogram import Router, types
from aiogram.filters import Command

from bot.db_repo.unit_of_work import new_uow

router = Router(name="plants_cmd")

@router.message(Command("add_plant"))
async def add_plant(m: types.Message):
    # имя после команды, либо дефолт
    raw = (m.text or "").split(maxsplit=1)
    name = raw[1].strip() if len(raw) > 1 else "Растение"

    async with new_uow() as uow:
        user = await uow.users.get_or_create(m.from_user.id)
        # простое создание без вида
        await uow.plants.create(user_id=user.id, name=name)

    await m.answer(f"Добавлено: <b>{name}</b>\n"
                   f"Теперь можно настроить расписание: /set_schedule")