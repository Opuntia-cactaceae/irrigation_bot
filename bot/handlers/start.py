# handlers/start.py
from aiogram import Router, types
from .db import SessionLocal
from ..models import User

router = Router()

@router.message(commands={"start"})
async def start(m: types.Message):
    async with SessionLocal() as s:
        user = (await s.execute(
            User.__table__.select().where(User.tg_user_id == m.from_user.id)
        )).scalar_one_or_none()
        if not user:
            user = User(tg_user_id=m.from_user.id)  # tz по умолчанию
            s.add(user)
            await s.commit()
    await m.answer("Привет! Я помогу с поливом 🌿\nДобавь растение: /add_plant")