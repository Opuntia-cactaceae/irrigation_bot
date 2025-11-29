# bot/db_repo/repositories/action_logs.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select, delete, desc, func, and_, or_
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
    ShareLink,
    ShareMember,
    ShareMemberStatus,
)
from .base import BaseRepo


class ActionLogsRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def _resolve_owner_from_ids(
            self, *, schedule_id: int | None, plant_id: int | None
    ) -> int | None:

        if schedule_id is not None:
            res = await self.session.execute(
                select(Plant.user_id).join(Schedule, Schedule.plant_id == Plant.id).where(Schedule.id == schedule_id)
            )
            owner = res.scalar_one_or_none()
            if owner is not None:
                return owner


        if plant_id is not None:
            res = await self.session.execute(
                select(Plant.user_id).where(Plant.id == plant_id)
            )
            owner = res.scalar_one_or_none()
            if owner is not None:
                return owner

        return None

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
            owner_user_id: int | None = None,
            share_id: int | None = None,
            share_member_id: int | None = None,
    ) -> ActionLog:

        if owner_user_id is None:
            owner_user_id = await self._resolve_owner_from_ids(
                schedule_id=schedule_id, plant_id=plant_id
            )

        if plant_name_at_time is None:
            if plant_id is not None:
                res = await self.session.execute(
                    select(Plant.name).where(Plant.id == plant_id)
                )
                plant_name_at_time = res.scalar_one_or_none()

            elif schedule_id is not None:
                res = await self.session.execute(
                    select(Plant.name)
                    .join(Schedule, Schedule.plant_id == Plant.id)
                    .where(Schedule.id == schedule_id)
                )
                plant_name_at_time = res.scalar_one_or_none()

        log = ActionLog(
            user_id=user_id,
            owner_user_id=owner_user_id,
            plant_id=plant_id,
            schedule_id=schedule_id,
            action=action,
            status=status,
            source=source,
            done_at_utc=done_at_utc,
            plant_name_at_time=plant_name_at_time,
            note=note,
            share_id=share_id,
            share_member_id=share_member_id,
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
            # опционально позволим переопределить actor при автособытиях
            user_id: int | None = None,
            share_id: int | None = None,
            share_member_id: int | None = None,
    ) -> ActionLog:
        plant = schedule.plant
        return await self.create(
            user_id=(user_id or plant.user.id),  # actor (обычно владелец для авто-логов)
            owner_user_id=plant.user.id,  # владелец явно
            plant_id=plant.id,
            schedule_id=schedule.id,
            action=schedule.action,
            status=status,
            source=source,
            done_at_utc=done_at_utc,
            plant_name_at_time=plant.name,
            note=note,
            share_id=share_id,
            share_member_id=share_member_id,
        )

    async def create_manual(
            self,
            *,
            user: User,  # actor
            action: ActionType,
            plant: Plant | None = None,
            schedule: Schedule | None = None,
            status: ActionStatus = ActionStatus.DONE,
            done_at_utc: datetime | None = None,
            note: str | None = None,
            share_id: int | None = None,
            share_member_id: int | None = None,
    ) -> ActionLog:
        if schedule:
            owner_user_id = schedule.plant.user.id
            plant_id = schedule.plant.id
            schedule_id = schedule.id
            plant_name = schedule.plant.name
        elif plant:
            owner_user_id = plant.user.id
            plant_id = plant.id
            schedule_id = None
            plant_name = plant.name
        else:
            owner_user_id = user.id
            plant_id = None
            schedule_id = None
            plant_name = None

        return await self.create(
            user_id=user.id,
            owner_user_id=owner_user_id,
            plant_id=plant_id,
            schedule_id=schedule_id,
            action=action,
            status=status,
            source=ActionSource.MANUAL,
            done_at_utc=done_at_utc,
            plant_name_at_time=plant_name,
            note=note,
            share_id=share_id,
            share_member_id=share_member_id,
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

    async def list_for_history(
            self,
            user_id: int,
            *,
            limit: int = 100,
            offset: int = 0,
            action: ActionType | None = None,
            status: ActionStatus | None = None,
            since: datetime | None = None,
            until: datetime | None = None,
            with_relations: bool = False,
    ) -> Sequence[ActionLog]:
        conds = [
            or_(
                ActionLog.user_id == user_id,
                ActionLog.owner_user_id == user_id,
            )
        ]
        if action:
            conds.append(ActionLog.action == action)
        if status:
            conds.append(ActionLog.status == status)
        if since:
            conds.append(ActionLog.done_at_utc >= since)
        if until:
            conds.append(ActionLog.done_at_utc < until)

        q = (
            select(ActionLog)
            .where(and_(*conds))
            .order_by(desc(ActionLog.done_at_utc))
            .limit(limit)
            .offset(offset)
        )
        if with_relations:
            q = q.options(
                selectinload(ActionLog.plant),
                selectinload(ActionLog.schedule),
            )
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

    async def delete(self, log_id: int) -> None:
        await self.session.execute(delete(ActionLog).where(ActionLog.id == log_id))

    async def last_effective_done(self, schedule_id: int) -> tuple[Optional[datetime], Optional[ActionSource]]:
        q = (
            select(ActionLog.done_at_utc, ActionLog.source)
            .where(
                ActionLog.schedule_id == schedule_id,
            )
            .order_by(desc(ActionLog.done_at_utc))
            .limit(1)
        )
        row = (await self.session.execute(q)).first()
        if not row:
            return None, None
        return row[0], row[1]

    async def list_shared_for_subscriber(
            self,
            subscriber_user_id: int,
            *,
            limit: int = 50,
            offset: int = 0,
            action: ActionType | None = None,
            status: ActionStatus | None = None,
            since: datetime | None = None,
            until: datetime | None = None,
            with_relations: bool = False,
    ) -> Sequence[ActionLog]:
        """
        Возвращает логи из расшаренных расписаний, доступные подписчику.
        Условия видимости:
          - есть активное членство в шаре (ShareMember.status == ACTIVE)
          - сам шар активен (ShareLink.is_active == True)
          - политика видимости истории: COALESCE(ShareMember.show_history_override, ShareLink.show_history_default) == True
        Фильтры action/status/since/until применяются к ActionLog.
        """
        # базовые условия видимости
        conds = [
            ActionLog.share_id.is_not(None),
            ShareLink.id == ActionLog.share_id,
            ShareLink.is_active.is_(True),
            ShareMember.share_id == ShareLink.id,
            ShareMember.subscriber_user_id == subscriber_user_id,
            ShareMember.status == ShareMemberStatus.ACTIVE,
            func.coalesce(ShareMember.show_history_override, ShareLink.show_history_default).is_(True),
        ]

        # фильтры по самим логам
        if action:
            conds.append(ActionLog.action == action)
        if status:
            conds.append(ActionLog.status == status)
        if since:
            conds.append(ActionLog.done_at_utc >= since)
        if until:
            conds.append(ActionLog.done_at_utc < until)

        q = (
            select(ActionLog)
            .join(ShareLink, ShareLink.id == ActionLog.share_id)
            .join(ShareMember, and_(
                ShareMember.share_id == ShareLink.id,
                ShareMember.subscriber_user_id == subscriber_user_id,
            ))
            .where(and_(*conds))
            .order_by(desc(ActionLog.done_at_utc))
            .limit(limit)
            .offset(offset)
        )

        if with_relations:
            q = q.options(
                selectinload(ActionLog.plant),
                selectinload(ActionLog.schedule),
            )

        return (await self.session.execute(q)).scalars().all()