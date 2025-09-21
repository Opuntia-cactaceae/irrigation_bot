# db_repo/species.py
from typing import Optional, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .models import Species

class SpeciesRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_or_create(self, user_id: int, name: str) -> Species:
        q = select(Species).where(Species.user_id == user_id, Species.name == name)
        s = (await self.session.execute(q)).scalar_one_or_none()
        if s:
            return s
        s = Species(user_id=user_id, name=name)
        self.session.add(s)
        await self.session.flush()
        return s

    async def list_by_user(self, user_id: int) -> Sequence[Species]:
        q = select(Species).where(Species.user_id == user_id).order_by(Species.name.asc())
        return (await self.session.execute(q)).scalars().all()