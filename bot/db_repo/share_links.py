# bot/db_repo/share_links.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence, Iterable

from sqlalchemy import select, delete, and_, or_, func, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseRepo
from .models import (
    ShareLink,
    ShareLinkSchedule,
    ShareMember,
    Schedule,
    User,
)


class ShareLinksRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)


    async def create(
        self,
        owner_user_id: int,
        code: str,
        *,
        title: str | None = None,
        note: str | None = None,
        allow_complete_default: bool = True,
        show_history_default: bool = True,
        expires_at_utc: datetime | None = None,
        max_uses: int | None = None,
    ) -> ShareLink:
        sl = ShareLink(
            owner_user_id=owner_user_id,
            code=code,
            title=title,
            note=note,
            allow_complete_default=allow_complete_default,
            show_history_default=show_history_default,
            expires_at_utc=expires_at_utc,
            max_uses=max_uses,
        )
        return await self.add(sl)

    async def get(self, share_id: int) -> Optional[ShareLink]:
        return await self.session.get(ShareLink, share_id)

    async def get_with_relations(self, share_id: int) -> Optional[ShareLink]:
        q = (
            select(ShareLink)
            .where(ShareLink.id == share_id)
            .options(
                selectinload(ShareLink.owner),
                selectinload(ShareLink.schedules)
                .selectinload(ShareLinkSchedule.schedule)
                .selectinload(Schedule.plant),
                selectinload(ShareLink.members).selectinload(ShareMember.subscriber),
            )
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_by_owner(self, owner_user_id: int) -> Sequence[ShareLink]:
        q = select(ShareLink).where(ShareLink.owner_user_id == owner_user_id)
        return (await self.session.execute(q)).scalars().all()

    async def list_by_owner_with_relations(self, owner_user_id: int) -> Sequence[ShareLink]:
        q = (
            select(ShareLink)
            .where(ShareLink.owner_user_id == owner_user_id)
            .options(
                selectinload(ShareLink.schedules)
                .selectinload(ShareLinkSchedule.schedule)
                .selectinload(Schedule.plant),
                selectinload(ShareLink.members).selectinload(ShareMember.subscriber),
            )
        )
        return (await self.session.execute(q)).scalars().all()

    async def delete(self, share_id: int) -> None:
        await self.session.execute(delete(ShareLink).where(ShareLink.id == share_id))


    async def get_by_code(self, code: str) -> Optional[ShareLink]:
        q = select(ShareLink).where(ShareLink.code == code)
        return (await self.session.execute(q)).scalar_one_or_none()

    async def get_by_code_active(self, code: str, *, now_utc: datetime | None = None) -> Optional[ShareLink]:
        """
        Возвращает активный линк по коду с учётом is_active, expires_at и max_uses.
        """
        if now_utc is None:
            now_utc = datetime.now(timezone.utc)

        q = (
            select(ShareLink)
            .where(
                ShareLink.code == code,
                ShareLink.is_active.is_(True),
                or_(ShareLink.expires_at_utc.is_(None), ShareLink.expires_at_utc > now_utc),
                or_(ShareLink.max_uses.is_(None), ShareLink.uses_count < ShareLink.max_uses),
            )
            .options(selectinload(ShareLink.owner))
        )
        return (await self.session.execute(q)).scalar_one_or_none()


    async def set_active(self, share_id: int, active: bool) -> None:
        await self.session.execute(
            update(ShareLink).where(ShareLink.id == share_id).values(is_active=active)
        )

    async def increment_uses(self, share_id: int, by: int = 1) -> None:
        await self.session.execute(
            update(ShareLink)
            .where(ShareLink.id == share_id)
            .values(uses_count=ShareLink.uses_count + by)
        )

    async def update_defaults(
        self,
        share_id: int,
        *,
        allow_complete_default: bool | None = None,
        show_history_default: bool | None = None,
        title: str | None = None,
        note: str | None = None,
        expires_at_utc: datetime | None = None,
        max_uses: int | None = None,
        is_active: bool | None = None,
    ) -> None:
        values = {}
        if allow_complete_default is not None:
            values["allow_complete_default"] = allow_complete_default
        if show_history_default is not None:
            values["show_history_default"] = show_history_default
        if title is not None:
            values["title"] = title
        if note is not None:
            values["note"] = note
        if expires_at_utc is not None:
            values["expires_at_utc"] = expires_at_utc
        if max_uses is not None:
            values["max_uses"] = max_uses
        if is_active is not None:
            values["is_active"] = is_active

        if values:
            await self.session.execute(
                update(ShareLink).where(ShareLink.id == share_id).values(**values)
            )


    async def add_schedules(self, share_id: int, schedule_ids: Iterable[int]) -> Sequence[ShareLinkSchedule]:
        """
        Массовое добавление расписаний в линк. Уникальный ключ защитит от дублей.
        """
        created: list[ShareLinkSchedule] = []
        for sid in set(schedule_ids):
            created.append(await self.add(ShareLinkSchedule(share_id=share_id, schedule_id=sid)))
        return created

    async def remove_schedule(self, share_id: int, schedule_id: int) -> None:
        await self.session.execute(
            delete(ShareLinkSchedule).where(
                ShareLinkSchedule.share_id == share_id,
                ShareLinkSchedule.schedule_id == schedule_id,
            )
        )

    async def list_schedules(self, share_id: int) -> Sequence[Schedule]:
        q = (
            select(Schedule)
            .join(ShareLinkSchedule, ShareLinkSchedule.schedule_id == Schedule.id)
            .where(ShareLinkSchedule.share_id == share_id)
            .options(selectinload(Schedule.plant))
        )
        return (await self.session.execute(q)).scalars().all()

    async def list_members(self, share_id: int) -> Sequence[ShareMember]:
        q = (
            select(ShareMember)
            .where(ShareMember.share_id == share_id)
            .options(selectinload(ShareMember.subscriber))
        )
        return (await self.session.execute(q)).scalars().all()