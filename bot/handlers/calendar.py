# bot/handlers/calendar.py
from aiogram import Router, types
from aiogram.filters import Command
from datetime import datetime

from .calendar_inline import show_calendar_root

router = Router(name="calendar_cmd")

@router.message(Command("calendar"))
async def calendar_cmd(m: types.Message):
    now = datetime.utcnow()
    # по умолчанию: ближайшие (mode="upc"), без фильтров
    await show_calendar_root(
        m,
        year=now.year,
        month=now.month,
        action=None,
        plant_id=None,
        mode="upc",
        page=1,
    )