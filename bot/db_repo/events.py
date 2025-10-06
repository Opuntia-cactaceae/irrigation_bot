from __future__ import annotations

import enum
from typing import Optional, Sequence, TypeVar, Union, cast

from sqlalchemy import select, and_, desc
from sqlalchemy.ext.asyncio import AsyncSession

from .models import Event, ActionType, ActionSource
from .base import BaseRepo

ActionLike = Union[str, ActionType]
SourceLike = Union[str, ActionSource]

E = TypeVar("E", bound=enum.Enum)


def _coerce_enum(value: Union[str, E], enum_cls: type[E], field_name: str) -> E:
    if isinstance(value, enum_cls):
        return value

    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ValueError(f"{field_name} cannot be empty")

        s_lower = s.lower()
        s_upper = s.upper()

        for m in enum_cls:
            if str(m.value).lower() == s_lower:
                return m

        for m in enum_cls:
            if m.name.upper() == s_upper:
                return m

        raise ValueError(f"Unknown {field_name}: {value!r}")

    raise TypeError(
        f"Unsupported {field_name} type: {type(value)!r}; expected str or {enum_cls.__name__}"
    )


class EventsRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    async def create(
        self,
        plant_id: int,
        action: ActionLike,
        source: SourceLike = ActionSource.MANUAL,
        *,
        schedule_id: Optional[int] = None,
    ) -> Event:

        action_enum: ActionType = _coerce_enum(action, ActionType, "action")
        source_enum: ActionSource = _coerce_enum(source, ActionSource, "source")

        ev = Event(
            plant_id=plant_id,
            schedule_id=schedule_id,
            action=action_enum,
            source=source_enum,
        )
        return await self.add(ev)

    async def create_from_schedule(
        self,
        schedule,
        *,
        source: SourceLike = ActionSource.SCHEDULE,
    ) -> Event:

        return await self.create(
            plant_id=schedule.plant_id,
            action=cast(ActionType, schedule.action),
            source=source,
            schedule_id=schedule.id,
        )

    async def last_for_plant_action(
        self,
        plant_id: int,
        action: ActionLike,
    ) -> Optional[Event]:
        action_enum: ActionType = _coerce_enum(action, ActionType, "action")  # type: ignore[assignment]
        q = (
            select(Event)
            .where(and_(Event.plant_id == plant_id, Event.action == action_enum))
            .order_by(desc(Event.done_at_utc))
            .limit(1)
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_for_plant_action(
        self,
        plant_id: int,
        action: ActionLike,
        limit: int = 50,
    ) -> Sequence[Event]:
        action_enum: ActionType = _coerce_enum(action, ActionType, "action")  # type: ignore[assignment]
        q = (
            select(Event)
            .where(and_(Event.plant_id == plant_id, Event.action == action_enum))
            .order_by(desc(Event.done_at_utc))
            .limit(limit)
        )
        return (await self.session.execute(q)).scalars().all()