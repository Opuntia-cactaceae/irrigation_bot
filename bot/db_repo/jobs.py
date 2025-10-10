# bot/db_repo/jobs.py
from __future__ import annotations
from datetime import datetime
from typing import Optional, List

from sqlalchemy import select, func
from sqlalchemy.orm import selectinload

from .models import Schedule, Plant, ActionLog, ActionStatus


class JobsRepo:
    def __init__(self, session):
        self.session = session


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


