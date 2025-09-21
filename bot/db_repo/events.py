from typing import Optional, Sequence
from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Event, ActionType
from .base import BaseRepo

class EventsRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(self, plant_id: int, action: ActionType, source: str = "manual") -> Event:
        ev = Event(plant_id=plant_id, action=action, source=source)
        return await self.add(ev)

    async def last_for_plant_action(self, plant_id: int, action: ActionType) -> Optional[Event]:
        q = (
            select(Event)
            .where(and_(Event.plant_id == plant_id, Event.action == action))
            .order_by(desc(Event.done_at_utc))
            .limit(1)
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_for_plant_action(self, plant_id: int, action: ActionType, limit: int = 50) -> Sequence[Event]:
        q = (
            select(Event)
            .where(and_(Event.plant_id == plant_id, Event.action == action))
            .order_by(desc(Event.done_at_utc))
            .limit(limit)
        )
        return (await self.session.execute(q)).scalars().all()