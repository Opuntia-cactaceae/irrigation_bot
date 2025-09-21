# handlers/schedule.py
from aiogram import Router, types
from aiogram.filters import Command
from datetime import time
from ..db import SessionLocal
from ..models import Plant, Schedule, ScheduleType
from ..scheduler import plan_next_for_plant
from ..config import settings
from aiogram import Bot

router = Router()

@router.message(Command("set_schedule"))
async def set_schedule(m: types.Message, bot: Bot):
    # пример парсинга простых команд:
    # /set_schedule 1 interval 3 09:30
    # /set_schedule 2 weekly Mon,Thu 09:00
    try:
        _, plant_id, kind, spec, hm = m.text.split(maxsplit=4)
        h, mi = map(int, hm.split(":"))
        local_t = time(hour=h, minute=mi)
    except Exception:
        return await m.answer("Формат:\ninterval: /set_schedule <plant_id> interval <days> HH:MM\nweekly: /set_schedule <plant_id> weekly <Mon,Wed,...> HH:MM")

    async with SessionLocal() as s:
        plant = await s.get(Plant, int(plant_id))
        if not plant: return await m.answer("Растение не найдено.")
        if kind == "interval":
            sch = Schedule(plant_id=plant.id, type=ScheduleType.INTERVAL, interval_days=int(spec), local_time=local_t, active=True)
        else:
            names = [x.strip().lower() for x in spec.split(",")]
            order = ["mon","tue","wed","thu","fri","sat","sun"]
            mask = 0
            for n in names:
                if n in order:
                    mask |= 1 << order.index(n)
            sch = Schedule(plant_id=plant.id, type=ScheduleType.WEEKLY, weekly_mask=mask, local_time=local_t, active=True)
        # upsert
        if plant.schedule:
            await s.delete(plant.schedule)
            await s.flush()
        s.add(sch)
        await s.commit()
        await s.refresh(plant)
        await plan_next_for_plant(bot, plant)

    await m.answer("Расписание сохранено ✅")