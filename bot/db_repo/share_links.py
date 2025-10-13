# bot/db_repo/share_links.py
from __future__ import annotations

from datetime import datetime, timezone
from typing import Optional, Sequence, Iterable

from sqlalchemy import select, delete, or_, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.dialects.postgresql import insert  # для bulk_add с on_conflict_do_nothing

from .base import BaseRepo
from .models import (
    ShareLink,
    ShareLinkSchedule,
    ShareMember,
    Schedule,
    Plant,
)


class ShareLinksRepo(BaseRepo):
    """
    Единый репозиторий:
    - Работа с ShareLink (CRUD, активность, счётчики, поиск по коду).
    - Работа с привязками ShareLink <-> Schedule (создание/удаление пары, пакетные операции,
      выборки списков Schedule для конкретного ShareLink и списков ShareLink для конкретного Schedule).
    """

    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # -------------------------------------------------------------------------
    # ShareLink: CRUD и бизнес-логика
    # -------------------------------------------------------------------------

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
        """
        Глубокая загрузка:
          - owner
          - schedules -> schedule -> plant -> plant.user
          - members -> subscriber
        """
        q = (
            select(ShareLink)
            .where(ShareLink.id == share_id)
            .options(
                selectinload(ShareLink.owner),
                selectinload(ShareLink.schedules)
                .selectinload(ShareLinkSchedule.schedule)
                .selectinload(Schedule.plant)
                .selectinload(Plant.user),
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
                .selectinload(Schedule.plant)
                .selectinload(Plant.user),
                selectinload(ShareLink.members).selectinload(ShareMember.subscriber),
            )
        )
        return (await self.session.execute(q)).scalars().all()

    async def delete(self, share_id: int) -> bool:
        """
        Удаляет ShareLink. Возвращает True, если что-то удалили.
        """
        res = await self.session.execute(delete(ShareLink).where(ShareLink.id == share_id))
        return (res.rowcount or 0) > 0

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

    async def set_active(self, share_id: int, active: bool) -> bool:
        res = await self.session.execute(
            update(ShareLink).where(ShareLink.id == share_id).values(is_active=active)
        )
        return (res.rowcount or 0) > 0

    async def increment_uses(self, share_id: int, by: int = 1) -> bool:
        res = await self.session.execute(
            update(ShareLink)
            .where(ShareLink.id == share_id)
            .values(uses_count=ShareLink.uses_count + by)
        )
        return (res.rowcount or 0) > 0

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
        # флаги для явного "сброса" в NULL
        clear_title: bool = False,
        clear_note: bool = False,
        clear_expires_at: bool = False,
    ) -> bool:
        """
        Обновляет поля ShareLink. По умолчанию None означает "не изменять".
        Для обнуления используйте clear_* флаги.
        """
        values: dict[str, object] = {}

        if allow_complete_default is not None:
            values["allow_complete_default"] = allow_complete_default
        if show_history_default is not None:
            values["show_history_default"] = show_history_default

        if title is not None:
            values["title"] = title
        elif clear_title:
            values["title"] = None

        if note is not None:
            values["note"] = note
        elif clear_note:
            values["note"] = None

        if expires_at_utc is not None:
            values["expires_at_utc"] = expires_at_utc
        elif clear_expires_at:
            values["expires_at_utc"] = None

        if max_uses is not None:
            values["max_uses"] = max_uses
        if is_active is not None:
            values["is_active"] = is_active

        if not values:
            return False

        res = await self.session.execute(
            update(ShareLink).where(ShareLink.id == share_id).values(**values)
        )
        return (res.rowcount or 0) > 0

    # -------------------------------------------------------------------------
    # Связи ShareLink <-> Schedule
    # -------------------------------------------------------------------------

    async def add_pair(self, share_id: int, schedule_id: int) -> ShareLinkSchedule:
        """
        Добавляет связь share<->schedule.
        Полагается на уникальный индекс модели для защиты от дублей.
        """
        row = ShareLinkSchedule(share_id=share_id, schedule_id=schedule_id)
        return await self.add(row)

    async def exists_pair(self, share_id: int, schedule_id: int) -> bool:
        q = select(ShareLinkSchedule.id).where(
            ShareLinkSchedule.share_id == share_id,
            ShareLinkSchedule.schedule_id == schedule_id,
        )
        return (await self.session.execute(q)).scalar_one_or_none() is not None

    async def delete_pair(self, share_id: int, schedule_id: int) -> bool:
        res = await self.session.execute(
            delete(ShareLinkSchedule).where(
                ShareLinkSchedule.share_id == share_id,
                ShareLinkSchedule.schedule_id == schedule_id,
            )
        )
        return (res.rowcount or 0) > 0

    async def bulk_add(self, share_id: int, schedule_ids: Iterable[int]) -> int:
        """
        Массовое добавление связей через INSERT .. ON CONFLICT DO NOTHING.
        Возвращает количество вставленных строк (может быть 0, если всё было).
        Требует PostgreSQL.
        """
        values = [{"share_id": share_id, "schedule_id": sid} for sid in set(schedule_ids)]
        if not values:
            return 0

        stmt = (
            insert(ShareLinkSchedule)
            .values(values)
            .on_conflict_do_nothing(
                index_elements=[ShareLinkSchedule.share_id, ShareLinkSchedule.schedule_id]
            )
        )
        res = await self.session.execute(stmt)
        return int(res.rowcount or 0)

    async def bulk_remove(self, share_id: int, schedule_ids: Iterable[int]) -> int:
        """
        Пакетное удаление связей по списку schedule_id. Возвращает количество удалённых строк.
        """
        ids_set = set(schedule_ids)
        if not ids_set:
            return 0
        res = await self.session.execute(
            delete(ShareLinkSchedule).where(
                ShareLinkSchedule.share_id == share_id,
                ShareLinkSchedule.schedule_id.in_(ids_set),
            )
        )
        return int(res.rowcount or 0)

    async def list_schedules(self, share_id: int) -> Sequence[Schedule]:
        """
        Вернуть все Schedule, привязанные к ShareLink (с plant и его user).
        """
        q = (
            select(Schedule)
            .join(ShareLinkSchedule, ShareLinkSchedule.schedule_id == Schedule.id)
            .where(ShareLinkSchedule.share_id == share_id)
            .options(
                selectinload(Schedule.plant).selectinload(Plant.user)
            )
        )
        return (await self.session.execute(q)).scalars().all()

    async def list_links(self, schedule_id: int) -> Sequence[ShareLink]:
        """
        Вернуть ShareLink, в которых участвует данный Schedule.
        """
        q = (
            select(ShareLink)
            .join(ShareLinkSchedule, ShareLinkSchedule.share_id == ShareLink.id)
            .where(ShareLinkSchedule.schedule_id == schedule_id)
        )
        return (await self.session.execute(q)).scalars().all()