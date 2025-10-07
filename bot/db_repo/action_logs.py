# bot/db_repo/repositories/action_logs.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select, delete, desc, func, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from bot.db_repo.models import (
    ActionLog,
    ActionType,
    ActionStatus,
    ActionSource,
    Plant,
    Schedule,
    User,
)
from .base import BaseRepo


class ActionLogsRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ---------- Create ----------
    async def create(
        self,
        *,
        user_id: int,
        action: ActionType,
        status: ActionStatus,
        source: ActionSource,
        plant_id: int | None = None,
        schedule_id: int | None = None,
        done_at_utc: datetime | None = None,
        plant_name_at_time: str | None = None,
        note: str | None = None,
    ) -> ActionLog:
        log = ActionLog(
            user_id=user_id,
            plant_id=plant_id,
            schedule_id=schedule_id,
            action=action,
            status=status,
            source=source,
            done_at_utc=done_at_utc,
            plant_name_at_time=plant_name_at_time,
            note=note,
        )
        return await self.add(log)

    # Удобные конструкторы от расписания/растения
    async def create_from_schedule(
        self,
        *,
        schedule: Schedule,
        status: ActionStatus,
        source: ActionSource = ActionSource.SCHEDULE,
        done_at_utc: datetime | None = None,
        note: str | None = None,
    ) -> ActionLog:
        plant = schedule.plant
        print("in create_from_schedule")
        return await self.create(
            user_id=plant.user.id,
            plant_id=plant.id,
            schedule_id=schedule.id,
            action=schedule.action,
            status=status,
            source=source,
            done_at_utc=done_at_utc,
            plant_name_at_time=plant.name,
            note=note,
        )

    async def create_manual(
        self,
        *,
        user: User,
        action: ActionType,
        plant: Plant | None = None,
        schedule: Schedule | None = None,
        status: ActionStatus = ActionStatus.DONE,
        done_at_utc: datetime | None = None,
        note: str | None = None,
    ) -> ActionLog:
        print("inCRCR")
        return await self.create(
            user_id=user.id,
            plant_id=plant.id if plant else None,
            schedule_id=schedule.id if schedule else None,
            action=action,
            status=status,
            source=ActionSource.MANUAL,
            done_at_utc=done_at_utc,
            plant_name_at_time=(plant.name if plant else None),
            note=note,
        )

    # ---------- Get / lists ----------
    async def get(self, log_id: int) -> Optional[ActionLog]:
        return await self.session.get(ActionLog, log_id)

    async def get_with_relations(self, log_id: int) -> Optional[ActionLog]:
        q = (
            select(ActionLog)
            .where(ActionLog.id == log_id)
            .options(
                selectinload(ActionLog.schedule),
                selectinload(ActionLog.plant),
            )
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_by_user(
        self,
        user_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        action: ActionType | None = None,
        status: ActionStatus | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        with_relations: bool = False,
    ) -> Sequence[ActionLog]:
        conds = [ActionLog.user_id == user_id]
        if action:
            conds.append(ActionLog.action == action)
        if status:
            conds.append(ActionLog.status == status)
        if since:
            conds.append(ActionLog.done_at_utc >= since)
        if until:
            conds.append(ActionLog.done_at_utc < until)

        q = select(ActionLog).where(and_(*conds)).order_by(desc(ActionLog.done_at_utc)).limit(limit).offset(offset)
        if with_relations:
            q = q.options(selectinload(ActionLog.plant), selectinload(ActionLog.schedule))
        return (await self.session.execute(q)).scalars().all()

    async def list_by_plant(
        self,
        plant_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        with_relations: bool = False,
    ) -> Sequence[ActionLog]:
        q = (
            select(ActionLog)
            .where(ActionLog.plant_id == plant_id)
            .order_by(desc(ActionLog.done_at_utc))
            .limit(limit)
            .offset(offset)
        )
        if with_relations:
            q = q.options(selectinload(ActionLog.schedule))
        return (await self.session.execute(q)).scalars().all()

    async def list_by_schedule(
        self,
        schedule_id: int,
        *,
        limit: int = 50,
        offset: int = 0,
        with_relations: bool = False,
    ) -> Sequence[ActionLog]:
        q = (
            select(ActionLog)
            .where(ActionLog.schedule_id == schedule_id)
            .order_by(desc(ActionLog.done_at_utc))
            .limit(limit)
            .offset(offset)
        )
        if with_relations:
            q = q.options(selectinload(ActionLog.plant))
        return (await self.session.execute(q)).scalars().all()

    # Последняя отметка "выполнено" — полезно для расчёта следующего запуска
    async def last_done_for_schedule(self, schedule_id: int) -> Optional[datetime]:
        q = (
            select(ActionLog.done_at_utc)
            .where(
                ActionLog.schedule_id == schedule_id,
                ActionLog.status == ActionStatus.DONE,
            )
            .order_by(desc(ActionLog.done_at_utc))
            .limit(1)
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def last_done_for_plant_action(self, plant_id: int, action: ActionType) -> Optional[datetime]:
        q = (
            select(ActionLog.done_at_utc)
            .where(
                ActionLog.plant_id == plant_id,
                ActionLog.action == action,
                ActionLog.status == ActionStatus.DONE,
            )
            .order_by(desc(ActionLog.done_at_utc))
            .limit(1)
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    # ---------- Stats / interval ----------
    async def count_by_user(
        self,
        user_id: int,
        *,
        action: ActionType | None = None,
        status: ActionStatus | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
    ) -> int:
        conds = [ActionLog.user_id == user_id]
        if action:
            conds.append(ActionLog.action == action)
        if status:
            conds.append(ActionLog.status == status)
        if since:
            conds.append(ActionLog.done_at_utc >= since)
        if until:
            conds.append(ActionLog.done_at_utc < until)
        q = select(func.count()).where(and_(*conds))
        return (await self.session.execute(q)).scalar_one()

    async def list_between(
        self,
        *,
        user_id: int,
        since: datetime,
        until: datetime,
        with_relations: bool = False,
    ) -> Sequence[ActionLog]:
        q = (
            select(ActionLog)
            .where(
                ActionLog.user_id == user_id,
                ActionLog.done_at_utc >= since,
                ActionLog.done_at_utc < until,
            )
            .order_by(desc(ActionLog.done_at_utc))
        )
        if with_relations:
            q = q.options(selectinload(ActionLog.plant), selectinload(ActionLog.schedule))
        return (await self.session.execute(q)).scalars().all()

    # ---------- Delete ----------
    async def delete(self, log_id: int) -> None:
        await self.session.execute(delete(ActionLog).where(ActionLog.id == log_id))