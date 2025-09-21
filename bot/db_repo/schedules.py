from typing import Optional, Sequence
from sqlalchemy import select, delete, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Schedule, ActionType, ScheduleType
from .base import BaseRepo

class SchedulesRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def get(self, schedule_id: int) -> Optional[Schedule]:
        return await self.session.get(Schedule, schedule_id)

    async def get_for_plant_action(self, plant_id: int, action: ActionType) -> Optional[Schedule]:
        q = select(Schedule).where(
            and_(Schedule.plant_id == plant_id, Schedule.action == action)
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def upsert_interval(
        self, plant_id: int, action: ActionType, interval_days: int, local_time
    ) -> Schedule:
        existing = await self.get_for_plant_action(plant_id, action)
        if existing:
            existing.type = ScheduleType.INTERVAL
            existing.interval_days = interval_days
            existing.weekly_mask = None
            existing.local_time = local_time
            existing.active = True
            await self.session.flush()
            return existing
        sch = Schedule(
            plant_id=plant_id,
            action=action,
            type=ScheduleType.INTERVAL,
            interval_days=interval_days,
            weekly_mask=None,
            local_time=local_time,
            active=True,
        )
        return await self.add(sch)

    async def upsert_weekly(
        self, plant_id: int, action: ActionType, weekly_mask: int, local_time
    ) -> Schedule:
        existing = await self.get_for_plant_action(plant_id, action)
        if existing:
            existing.type = ScheduleType.WEEKLY
            existing.interval_days = None
            existing.weekly_mask = weekly_mask
            existing.local_time = local_time
            existing.active = True
            await self.session.flush()
            return existing
        sch = Schedule(
            plant_id=plant_id,
            action=action,
            type=ScheduleType.WEEKLY,
            interval_days=None,
            weekly_mask=weekly_mask,
            local_time=local_time,
            active=True,
        )
        return await self.add(sch)

    async def set_active(self, schedule_id: int, active: bool) -> None:
        await self.session.execute(
            update(Schedule).where(Schedule.id == schedule_id).values(active=active)
        )

    async def delete_for_plant_action(self, plant_id: int, action: ActionType) -> None:
        await self.session.execute(
            delete(Schedule).where(
                and_(Schedule.plant_id == plant_id, Schedule.action == action)
            )
        )

    async def list_active(self) -> Sequence[Schedule]:
        q = select(Schedule).where(Schedule.active.is_(True))
        return (await self.session.execute(q)).scalars().all()