# handlers/calendar.py
from aiogram import Router, types
from aiogram.filters import Command
from datetime import datetime
from ..db import SessionLocal
from ..models import User, Plant
from ..services.calendar import month_overview

router = Router()

@router.message(Command("calendar"))
async def calendar_cmd(m: types.Message):
    parts = m.text.split()
    now = datetime.utcnow()
    year, month = (now.year, now.month) if len(parts) == 1 else map(int, parts[1:3])
    async with SessionLocal() as s:
        user = (await s.execute(User.__table__.select().where(User.tg_user_id==m.from_user.id))).scalar_one()
        plants = (await s.execute(Plant.__table__.select().where(Plant.user_id==user.id))).all()
    txt = month_overview(user, [Plant(**row._mapping) for row in plants], year, month)
    await m.answer(txt)