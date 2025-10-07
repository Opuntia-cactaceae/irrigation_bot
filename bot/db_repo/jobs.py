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



    async def get_last_effective_done_utc(self, schedule_id: int) -> Optional[datetime]:
        """
        Возвращает опорную точку для перепланировки — последнее успешное выполнение.
        """
        q = select(func.max(ActionLog.done_at_utc)).where(
            ActionLog.schedule_id == schedule_id,
            ActionLog.status == ActionStatus.DONE,
        )
        res = await self.session.execute(q)
        return res.scalar_one_or_none()

