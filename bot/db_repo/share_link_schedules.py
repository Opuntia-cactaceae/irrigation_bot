# bot/db_repo/share_link_schedules.py
from __future__ import annotations

from typing import Optional, Sequence, Iterable

from sqlalchemy import select, delete
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseRepo
from .models import (
    ShareLinkSchedule,
    ShareLink,
    Schedule,
    Plant,
)


class ShareLinkSchedulesRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)


    async def create(self, share_id: int, schedule_id: int) -> ShareLinkSchedule:
        """
        Добавляет связь share<->schedule. Уникальный индекс в модели защитит от дублей.
        """
        row = ShareLinkSchedule(share_id=share_id, schedule_id=schedule_id)
        return await self.add(row)

    async def get(self, link_schedule_id: int) -> Optional[ShareLinkSchedule]:
        return await self.session.get(ShareLinkSchedule, link_schedule_id)

    async def get_with_relations(self, link_schedule_id: int) -> Optional[ShareLinkSchedule]:
        q = (
            select(ShareLinkSchedule)
            .where(ShareLinkSchedule.id == link_schedule_id)
            .options(
                selectinload(ShareLinkSchedule.share).selectinload(ShareLink.owner),
                selectinload(ShareLinkSchedule.schedule)
                .selectinload(Schedule.plant)
                .selectinload(Plant.user),
            )
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def delete(self, link_schedule_id: int) -> None:
        await self.session.execute(
            delete(ShareLinkSchedule).where(ShareLinkSchedule.id == link_schedule_id)
        )


    async def list_by_share(self, share_id: int) -> Sequence[ShareLinkSchedule]:
        q = select(ShareLinkSchedule).where(ShareLinkSchedule.share_id == share_id)
        return (await self.session.execute(q)).scalars().all()

    async def list_by_share_with_relations(self, share_id: int) -> Sequence[ShareLinkSchedule]:
        q = (
            select(ShareLinkSchedule)
            .where(ShareLinkSchedule.share_id == share_id)
            .options(
                selectinload(ShareLinkSchedule.share).selectinload(ShareLink.owner),
                selectinload(ShareLinkSchedule.schedule)
                .selectinload(Schedule.plant)
                .selectinload(Plant.user),
            )
        )
        return (await self.session.execute(q)).scalars().all()

    async def list_links_by_schedule(self, schedule_id: int) -> Sequence[ShareLinkSchedule]:
        q = select(ShareLinkSchedule).where(ShareLinkSchedule.schedule_id == schedule_id)
        return (await self.session.execute(q)).scalars().all()

    async def list_schedules(self, share_id: int) -> Sequence[Schedule]:
        """
        Удобный метод: вернуть сами Schedule для конкретного ShareLink.
        """
        q = (
            select(Schedule)
            .join(ShareLinkSchedule, ShareLinkSchedule.schedule_id == Schedule.id)
            .where(ShareLinkSchedule.share_id == share_id)
            .options(selectinload(Schedule.plant))
        )
        return (await self.session.execute(q)).scalars().all()

    async def list_links(self, schedule_id: int) -> Sequence[ShareLink]:
        """
        Удобный метод: вернуть ShareLink, в которых участвует данный Schedule.
        """
        q = (
            select(ShareLink)
            .join(ShareLinkSchedule, ShareLinkSchedule.share_id == ShareLink.id)
            .where(ShareLinkSchedule.schedule_id == schedule_id)
        )
        return (await self.session.execute(q)).scalars().all()


    async def exists(self, share_id: int, schedule_id: int) -> bool:
        q = select(ShareLinkSchedule.id).where(
            ShareLinkSchedule.share_id == share_id,
            ShareLinkSchedule.schedule_id == schedule_id,
        )
        return (await self.session.execute(q)).scalar_one_or_none() is not None

    async def delete_pair(self, share_id: int, schedule_id: int) -> None:
        await self.session.execute(
            delete(ShareLinkSchedule).where(
                ShareLinkSchedule.share_id == share_id,
                ShareLinkSchedule.schedule_id == schedule_id,
            )
        )

    async def bulk_add(self, share_id: int, schedule_ids: Iterable[int]) -> Sequence[ShareLinkSchedule]:
        """
        Массовое добавление связей; дубликаты отфильтруем на прикладном уровне.
        """
        created: list[ShareLinkSchedule] = []
        q_exist = select(ShareLinkSchedule.schedule_id).where(ShareLinkSchedule.share_id == share_id)
        existing = set((await self.session.execute(q_exist)).scalars().all())

        for sid in set(schedule_ids):
            if sid in existing:
                continue
            created.append(await self.add(ShareLinkSchedule(share_id=share_id, schedule_id=sid)))
        return created

    async def bulk_remove(self, share_id: int, schedule_ids: Iterable[int]) -> None:
        ids_set = set(schedule_ids)
        if not ids_set:
            return
        await self.session.execute(
            delete(ShareLinkSchedule).where(
                ShareLinkSchedule.share_id == share_id,
                ShareLinkSchedule.schedule_id.in_(ids_set),
            )
        )