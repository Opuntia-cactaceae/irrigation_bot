# db_repo/users.py
from typing import Optional
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from .models import User
from .base import BaseRepo


class UsersRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(self, tg_user_id: int) -> Optional[User]:
        q = select(User).where(User.tg_user_id == tg_user_id)
        return (await self.session.execute(q)).scalar_one_or_none()

    async def create(self, tg_user_id: int, tz: str = "Europe/Amsterdam") -> User:
        user = User(tg_user_id=tg_user_id, tz=tz)
        await self.add(user)
        await self.session.flush()
        return user

    async def set_timezone(self, user_id: int, tz: str) -> None:
        q = update(User).where(User.id == user_id).values(tz=tz)
        await self.session.execute(q)