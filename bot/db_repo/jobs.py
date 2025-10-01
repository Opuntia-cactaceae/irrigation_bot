# bot/db_repo/jobs.py
from __future__ import annotations
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from .models import Schedule, Plant, Event


class JobsRepo:


    def __init__(self, session):
        self.session = session

    # ---------- READ ----------

    async def get_schedule(self, schedule_id: int) -> Optional[Schedule]:

        return await self.session.get(
            Schedule,
            schedule_id,
            options=(selectinload(Schedule.plant).selectinload(Plant.user),),
        )

    async def get_active_schedules(self) -> List[Schedule]:

        q = (
            select(Schedule)
            .where(Schedule.active.is_(True))
            .options(selectinload(Schedule.plant).selectinload(Plant.user))
        )
        return list((await self.session.execute(q)).scalars().all())

    async def get_last_event_time(self, schedule_id: int) -> Optional[datetime]:

        q = select(func.max(Event.done_at_utc)).where(Event.schedule_id == schedule_id)
        res = await self.session.execute(q)
        return res.scalar_one_or_none()

    # ---------- WRITE ----------

    async def log_event(self, schedule_id: int) -> int:

        sch = await self.session.get(
            Schedule, schedule_id, options=(selectinload(Schedule.plant),)
        )
        if not sch:
            return 0

        ev = Event(
            plant_id=sch.plant.id,
            schedule_id=sch.id,
            action=sch.action,
            source="auto",
        )
        self.session.add(ev)
        await self.session.flush()  # получим ev.id без коммита
        return ev.id