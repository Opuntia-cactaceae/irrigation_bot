# bot/db_repo/share_members.py
from __future__ import annotations

from typing import Optional, Sequence

from sqlalchemy import select, delete, update, and_
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseRepo
from .models import (
    ShareMember,
    ShareMemberStatus,
    ShareLink,
    User,
)


class ShareMembersRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)


    async def create(
        self,
        share_id: int,
        subscriber_user_id: int,
        *,
        status: ShareMemberStatus = ShareMemberStatus.ACTIVE,
        can_complete_override: bool | None = None,
        show_history_override: bool | None = None,
        muted: bool = False,
    ) -> ShareMember:
        row = ShareMember(
            share_id=share_id,
            subscriber_user_id=subscriber_user_id,
            status=status,
            can_complete_override=can_complete_override,
            show_history_override=show_history_override,
            muted=muted,
        )
        return await self.add(row)

    async def get(self, member_id: int) -> Optional[ShareMember]:
        return await self.session.get(ShareMember, member_id)

    async def get_with_relations(self, member_id: int) -> Optional[ShareMember]:
        q = (
            select(ShareMember)
            .where(ShareMember.id == member_id)
            .options(
                selectinload(ShareMember.share).selectinload(ShareLink.owner),
                selectinload(ShareMember.subscriber),
            )
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def delete(self, member_id: int) -> None:
        await self.session.execute(delete(ShareMember).where(ShareMember.id == member_id))


    async def find(self, share_id: int, subscriber_user_id: int) -> Optional[ShareMember]:
        q = select(ShareMember).where(
            ShareMember.share_id == share_id,
            ShareMember.subscriber_user_id == subscriber_user_id,
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_by_share(self, share_id: int) -> Sequence[ShareMember]:
        q = select(ShareMember).where(ShareMember.share_id == share_id)
        return (await self.session.execute(q)).scalars().all()

    async def list_by_share_with_relations(self, share_id: int) -> Sequence[ShareMember]:
        q = (
            select(ShareMember)
            .where(ShareMember.share_id == share_id)
            .options(
                selectinload(ShareMember.share).selectinload(ShareLink.owner),
                selectinload(ShareMember.subscriber),
            )
        )
        return (await self.session.execute(q)).scalars().all()

    async def list_by_user(self, subscriber_user_id: int) -> Sequence[ShareMember]:
        q = select(ShareMember).where(ShareMember.subscriber_user_id == subscriber_user_id)
        return (await self.session.execute(q)).scalars().all()

    async def list_active_by_share(self, share_id: int) -> Sequence[ShareMember]:
        q = select(ShareMember).where(
            ShareMember.share_id == share_id,
            ShareMember.status == ShareMemberStatus.ACTIVE,
        )
        return (await self.session.execute(q)).scalars().all()


    async def set_status(self, member_id: int, status: ShareMemberStatus) -> None:
        await self.session.execute(
            update(ShareMember).where(ShareMember.id == member_id).values(status=status)
        )

    async def set_muted(self, member_id: int, muted: bool) -> None:
        await self.session.execute(
            update(ShareMember).where(ShareMember.id == member_id).values(muted=muted)
        )

    async def set_overrides(
        self,
        member_id: int,
        *,
        can_complete_override: bool | None,
        show_history_override: bool | None,
    ) -> None:
        await self.session.execute(
            update(ShareMember)
            .where(ShareMember.id == member_id)
            .values(
                can_complete_override=can_complete_override,
                show_history_override=show_history_override,
            )
        )

    async def clear_overrides(self, member_id: int) -> None:
        await self.session.execute(
            update(ShareMember)
            .where(ShareMember.id == member_id)
            .values(can_complete_override=None, show_history_override=None)
        )

    async def remove_by_pair(self, share_id: int, subscriber_user_id: int) -> None:
        await self.session.execute(
            delete(ShareMember).where(
                ShareMember.share_id == share_id,
                ShareMember.subscriber_user_id == subscriber_user_id,
            )
        )


    async def exists_active(self, share_id: int, subscriber_user_id: int) -> bool:
        q = select(ShareMember.id).where(
            ShareMember.share_id == share_id,
            ShareMember.subscriber_user_id == subscriber_user_id,
            ShareMember.status == ShareMemberStatus.ACTIVE,
        )
        return (await self.session.execute(q)).scalar_one_or_none() is not None