# db_repo/species.py
from typing import Optional, Sequence
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from .models import Species


class SpeciesRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, species_id: int) -> Optional[Species]:
        return await self.session.get(Species, species_id)

    async def get_by_name(self, user_id: int, name: str) -> Optional[Species]:
        q = select(Species).where(Species.user_id == user_id, Species.name == name)
        return (await self.session.execute(q)).scalar_one_or_none()

    async def create(self, user_id: int, name: str) -> Species:
        s = Species(user_id=user_id, name=name)
        self.session.add(s)
        await self.session.flush()
        return s

    async def update(self, species_id: int, **fields) -> None:
        obj = await self.get(species_id)
        if obj is None:
            return
        for k, v in fields.items():
            if hasattr(obj, k):
                setattr(obj, k, v)
        await self.session.flush()

    async def delete(self, species_id: int) -> None:
        obj = await self.get(species_id)
        if obj is not None:
            await self.session.delete(obj)
            await self.session.flush()

    async def list_by_user(self, user_id: int) -> Sequence[Species]:
        q = select(Species).where(Species.user_id == user_id).order_by(Species.name.asc())
        return (await self.session.execute(q)).scalars().all()