# handlers/watering.py
from aiogram import Router, types
from aiogram.filters import Command
from ..db import SessionLocal
from ..models import Plant, WaterEvent
from ..scheduler import plan_next_for_plant
from aiogram import Bot

router = Router()

@router.message(Command("water_now"))
async def water_now(m: types.Message, bot: Bot):
    # /water_now <plant_id>
    try:
        _, plant_id = m.text.split(maxsplit=1)
    except Exception:
        return await m.answer("Формат: /water_now <plant_id>")
    async with SessionLocal() as s:
        plant = await s.get(Plant, int(plant_id))
        if not plant: return await m.answer("Растение не найдено.")
        s.add(WaterEvent(plant_id=plant.id, source="manual"))
        await s.commit()
        # важное: пересчитываем и перезаписываем job
        await plan_next_for_plant(bot, plant)
    await m.answer("Отмечено 💧 Следующий полив пересчитан.")