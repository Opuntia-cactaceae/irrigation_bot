# bot/db_repo/action_pendings.py
from __future__ import annotations

from datetime import datetime
from typing import Optional, Sequence

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseRepo
from .models import (
    ActionPending,
    ActionType,
    ActionStatus,
    ActionSource,
)


class ActionPendingsRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ────────────────────────────── CREATE / GET ──────────────────────────────
    async def create(
        self,
        *,
        schedule_id: int,
        plant_id: int,
        owner_user_id: int,
        action: ActionType,
        planned_run_at_utc: datetime,
    ) -> ActionPending:
        row = ActionPending(
            schedule_id=schedule_id,
            plant_id=plant_id,
            owner_user_id=owner_user_id,
            action=action,
            planned_run_at_utc=planned_run_at_utc,
        )
        return await self.add(row)

    async def get(self, pending_id: int) -> Optional[ActionPending]:
        return await self.session.get(ActionPending, pending_id)

    async def get_for_update(self, pending_id: int) -> Optional[ActionPending]:
        """
        Забираем pending с блокировкой строки (для защиты от гонок при кликах).
        """
        q = select(ActionPending).where(ActionPending.id == pending_id).with_for_update()
        return (await self.session.execute(q)).scalar_one_or_none()

    async def find_by_unique(self, *, schedule_id: int, planned_run_at_utc: datetime) -> Optional[ActionPending]:
        """
        Ищет по уникальной паре (schedule_id, planned_run_at_utc).
        """
        q = select(ActionPending).where(
            ActionPending.schedule_id == schedule_id,
            ActionPending.planned_run_at_utc == planned_run_at_utc,
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    # ────────────────────────────── LIST / QUERY ──────────────────────────────
    async def list_open_by_schedule(self, schedule_id: int) -> Sequence[ActionPending]:
        """
        Нерешённые pending'и по расписанию.
        """
        q = select(ActionPending).where(
            ActionPending.schedule_id == schedule_id,
            ActionPending.resolved_status.is_(None),
        )
        return (await self.session.execute(q)).scalars().all()

    async def list_resolved_since(self, *, schedule_id: int, since_utc: datetime) -> Sequence[ActionPending]:
        """
        Решённые pending'и по расписанию с указанного времени.
        """
        q = select(ActionPending).where(
            ActionPending.schedule_id == schedule_id,
            ActionPending.resolved_at_utc.is_not(None),
            ActionPending.resolved_at_utc >= since_utc,
        )
        return (await self.session.execute(q)).scalars().all()

    # ────────────────────────────── UPDATE / RESOLVE ──────────────────────────
    async def mark_resolved(
        self,
        *,
        pending_id: int,
        status: ActionStatus,
        source: ActionSource,
        by_user_id: int,
        at_utc: datetime,
        log_id: Optional[int],
    ) -> None:
        """
        Помечает pending как решённый и привязывает к action_log (если есть).
        """
        await self.session.execute(
            update(ActionPending)
            .where(ActionPending.id == pending_id)
            .values(
                resolved_status=status,
                resolved_source=source,
                resolved_by_user_id=by_user_id,
                resolved_at_utc=at_utc,
                resolved_by_log_id=log_id,
            )
        )


    async def clear_resolution(self, pending_id: int) -> None:
        """
        Сбрасывает резолюцию (на случай отката).
        """
        await self.session.execute(
            update(ActionPending)
            .where(ActionPending.id == pending_id)
            .values(
                resolved_status=None,
                resolved_source=None,
                resolved_by_user_id=None,
                resolved_at_utc=None,
                resolved_by_log_id=None,
            )
        )

    # ────────────────────────────── DELETE / CLEANUP ──────────────────────────
    async def delete(self, pending_id: int) -> None:
        await self.session.execute(delete(ActionPending).where(ActionPending.id == pending_id))

    async def cleanup_resolved_before(self, *, before_utc: datetime) -> int:
        """
        Удаляет решённые pending'и старше указанной даты.
        Возвращает число удалённых строк.
        """
        result = await self.session.execute(
            delete(ActionPending).where(
                ActionPending.resolved_at_utc.is_not(None),
                ActionPending.resolved_at_utc < before_utc,
            )
        )
        # result.rowcount может быть None в некоторых драйверах, поэтому приводим к int
        return int(result.rowcount or 0)

    async def delete_future_for_schedule(self, schedule_id: int, *, from_utc: datetime) -> int:
        """
        Удаляет ВСЕ будущие pending'и (planned_run_at_utc >= from_utc) для расписания.
        Возвращает количество удалённых строк.
        """
        stmt = (
            delete(ActionPending)
            .where(
                ActionPending.schedule_id == schedule_id,
                ActionPending.planned_run_at_utc >= from_utc,
            )
        )
        res = await self.session.execute(stmt)
        # В разных драйверах rowcount может быть None — норм.
        return getattr(res, "rowcount", None) or 0

    # ────────────────────────────── HELPERS ───────────────────────────────────
    async def is_resolved(self, pending_id: int) -> bool:
        q = select(ActionPending.resolved_status).where(ActionPending.id == pending_id)
        val = (await self.session.execute(q)).scalar_one_or_none()
        return val is not None