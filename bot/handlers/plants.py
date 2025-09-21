# handlers/plants.py
from aiogram import Router, types
from aiogram.filters import Command
from ..db import SessionLocal
from ..models import Plant, User

router = Router()

@router.message(Command("add_plant"))
async def add_plant(m: types.Message):
    name = m.text.replace("/add_plant", "").strip() or "Растение"
    async with SessionLocal() as s:
        user = (await s.execute(User.__table__.select().where(User.tg_user_id==m.from_user.id))).scalar_one()
        plant = Plant(user_id=user.id, name=name)
        s.add(plant)
        await s.commit()
    await m.answer(f"Добавлено: {name}\nТеперь задай расписание: /set_schedule")