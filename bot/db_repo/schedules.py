# bot/db_repo/schedules.py
from typing import Optional, Sequence, List
from datetime import time as dtime

from sqlalchemy import select, delete, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Schedule, ActionType, ScheduleType


class SchedulesRepo:
    """
    Репозиторий расписаний.

    Новая политика:
    - НЕ перезаписываем существующие расписания.
    - Создаём новые записи (независимые таймеры).
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------- READ ----------

    async def get(self, schedule_id: int) -> Optional[Schedule]:
        return await self.session.get(Schedule, schedule_id)

    async def list_active(self) -> Sequence[Schedule]:
        q = select(Schedule).where(Schedule.active.is_(True))
        return (await self.session.execute(q)).scalars().all()

    async def list_by_plant(self, plant_id: int) -> List[Schedule]:
        q = select(Schedule).where(Schedule.plant_id == plant_id).order_by(Schedule.id.desc())
        return list((await self.session.execute(q)).scalars().all())

    async def list_by_plant_action(self, plant_id: int, action: ActionType) -> List[Schedule]:
        q = (
            select(Schedule)
            .where(and_(Schedule.plant_id == plant_id, Schedule.action == action))
            .order_by(Schedule.id.desc())
        )
        return list((await self.session.execute(q)).scalars().all())

    # ---------- WRITE ----------

    async def create(
        self,
        *,
        plant_id: int,
        action: ActionType,
        type: str,
        local_time: dtime,
        interval_days: Optional[int] = None,
        weekly_mask: Optional[int] = None,
        active: bool = True,
    ) -> Schedule:
        """
        Создать НОВОЕ расписание.
        Поле `type` — строка: ScheduleType.INTERVAL / ScheduleType.WEEKLY.
        """
        sch = Schedule(
            plant_id=plant_id,
            action=action,
            type=type,
            interval_days=interval_days,
            weekly_mask=weekly_mask,
            local_time=local_time,
            active=active,
        )
        self.session.add(sch)
        await self.session.flush()  # чтобы sch.id появился без коммита
        return sch

    async def update(self, schedule_id: int, **fields) -> None:
        """
        Обновить произвольные поля расписания.
        """
        if not fields:
            return
        await self.session.execute(
            update(Schedule).where(Schedule.id == schedule_id).values(**fields)
        )

    async def set_active(self, schedule_id: int, active: bool) -> None:
        await self.update(schedule_id, active=active)

    async def delete(self, schedule_id: int) -> None:
        await self.session.execute(delete(Schedule).where(Schedule.id == schedule_id))

    async def delete_for_plant_action(self, plant_id: int, action: ActionType) -> None:
        """
        Массовое удаление — используется для команды «Удалить всё».
        """
        await self.session.execute(
            delete(Schedule).where(
                and_(Schedule.plant_id == plant_id, Schedule.action == action)
            )
        )

    # ---------- Совместимость (не рекомендуется к использованию) ----------

    async def get_for_plant_action(self, plant_id: int, action: ActionType) -> Optional[Schedule]:
        """
        Исторически возвращал «единственное» расписание для пары (plant, action).
        Т.к. теперь расписаний может быть несколько, метод сохраняем для совместимости,
        но он вернёт ПЕРВУЮ найденную запись (по возрастанию id не гарантируется).
        Лучше используйте list_by_plant_action().
        """
        q = select(Schedule).where(
            and_(Schedule.plant_id == plant_id, Schedule.action == action)
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def upsert_interval(
        self, plant_id: int, action: ActionType, interval_days: int, local_time: dtime
    ) -> Schedule:
        """
        СТАРОЕ ПОВЕДЕНИЕ upsert ЗАМЕНЯЛО существующее расписание.
        ТЕПЕРЬ — ВСЕГДА создаём НОВОЕ расписание (ничего не перетираем).
        """
        return await self.create(
            plant_id=plant_id,
            action=action,
            type=ScheduleType.INTERVAL,  # "interval"
            interval_days=interval_days,
            weekly_mask=None,
            local_time=local_time,
            active=True,
        )

    async def upsert_weekly(
        self, plant_id: int, action: ActionType, weekly_mask: int, local_time: dtime
    ) -> Schedule:
        """
        СТАРОЕ ПОВЕДЕНИЕ upsert ЗАМЕНЯЛО существующее расписание.
        ТЕПЕРЬ — ВСЕГДА создаём НОВОЕ расписание (ничего не перетираем).
        """
        return await self.create(
            plant_id=plant_id,
            action=action,
            type=ScheduleType.WEEKLY,  # "weekly"
            interval_days=None,
            weekly_mask=weekly_mask,
            local_time=local_time,
            active=True,
        )