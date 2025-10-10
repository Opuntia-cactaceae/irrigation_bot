from typing import Optional, Sequence
from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Plant
from .base import BaseRepo

class PlantsRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, user_id: int, name: str, species_id: int | None = None) -> Plant:
        p = Plant(user_id=user_id, name=name, species_id=species_id)
        return await self.add(p)

    async def get(self, plant_id: int) -> Optional[Plant]:
        return await self.session.get(Plant, plant_id)

    async def get_with_relations(self, plant_id: int) -> Optional[Plant]:
        q = (
            select(Plant)
            .where(Plant.id == plant_id)
            .options(
                selectinload(Plant.user),
                selectinload(Plant.species),
                selectinload(Plant.schedules)
            )
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_by_user(self, user_id: int) -> Sequence[Plant]:
        q = select(Plant).where(Plant.user_id == user_id).options(selectinload(Plant.species))
        return (await self.session.execute(q)).scalars().all()

    async def list_by_user_with_relations(self, user_id: int) -> Sequence[Plant]:
        q = (
            select(Plant)
            .where(Plant.user_id == user_id)
            .options(
                selectinload(Plant.species),
                selectinload(Plant.schedules)
            )
        )
        return (await self.session.execute(q)).scalars().all()

    async def delete(self, plant_id: int) -> None:
        await self.session.execute(delete(Plant).where(Plant.id == plant_id))