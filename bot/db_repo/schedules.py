# bot/db_repo/schedules.py
from typing import Optional, Sequence, List
from datetime import time as dtime

from sqlalchemy import select, delete, update, and_
from sqlalchemy.ext.asyncio import AsyncSession

from .models import (
    Schedule,
    ActionType,
    ScheduleType,
    Plant,
    User,
    ScheduleSubscription,
)


def _coerce_schedule_type(value) -> ScheduleType:
    """
    Мягко приводим вход к ScheduleType:
    - уже Enum -> вернуть как есть
    - строка ('interval'/'weekly', регистр не важен) -> Enum
    """
    if isinstance(value, ScheduleType):
        return value
    if isinstance(value, str):
        v = value.strip().lower()
        if v == "interval":
            return ScheduleType.INTERVAL
        if v == "weekly":
            return ScheduleType.WEEKLY
    raise ValueError(f"Unsupported schedule type: {value!r}")


class SchedulesRepo:
    """
    Репозиторий расписаний.

    Политика:
    - НЕ перезаписываем существующие расписания.
    - Создаём новые записи (независимые таймеры).
    - Поддержка кастом-действий (ActionType.CUSTOM) с полями custom_title/custom_note_template.
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
        q = (
            select(Schedule)
            .where(Schedule.plant_id == plant_id)
            .order_by(Schedule.id.desc())
        )
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
        type: ScheduleType | str,
        local_time: dtime,
        interval_days: Optional[int] = None,
        weekly_mask: Optional[int] = None,
        active: bool = True,
        # кастом-действие:
        custom_title: Optional[str] = None,
        custom_note_template: Optional[str] = None,
    ) -> Schedule:
        """
        Создать НОВОЕ расписание.
        Поле `type` — Enum ScheduleType (можно передать строку, она будет приведена).
        Для action=CUSTOM можно (и желательно) указать custom_title/custom_note_template.
        Для остальных action — custom_* будут обнулены.
        """
        type_enum = _coerce_schedule_type(type)

        if action != ActionType.CUSTOM:
            custom_title = None
            custom_note_template = None

        sch = Schedule(
            plant_id=plant_id,
            action=action,
            type=type_enum,
            interval_days=interval_days,
            weekly_mask=weekly_mask,
            local_time=local_time,
            active=active,
            custom_title=custom_title,
            custom_note_template=custom_note_template,
        )
        self.session.add(sch)
        await self.session.flush()
        return sch

    async def update(self, schedule_id: int, **fields) -> None:
        """
        Обновить произвольные поля расписания.
        Примечание: если action != CUSTOM — custom_* будут обнулены даже если передать.
        Также мягко приводим type (если передан) к ScheduleType.
        """
        if not fields:
            return


        if "type" in fields and fields["type"] is not None:
            fields["type"] = _coerce_schedule_type(fields["type"])


        new_action: Optional[ActionType] = fields.get("action")
        if new_action is not None and new_action != ActionType.CUSTOM:
            fields.setdefault("custom_title", None)
            fields.setdefault("custom_note_template", None)

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
