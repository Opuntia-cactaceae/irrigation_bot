# bot/db_repo/action_pending_messages.py
from __future__ import annotations

from typing import Optional, Sequence, Iterable

from sqlalchemy import select, delete, update
from sqlalchemy.orm import selectinload
from sqlalchemy.ext.asyncio import AsyncSession

from .base import BaseRepo
from .models import (
    ActionPendingMessage,
    ActionPending,
    ShareLink,
    ShareMember,
)


class ActionPendingMessagesRepo(BaseRepo):
    def __init__(self, session: AsyncSession) -> None:
        super().__init__(session)

    # ────────────────────────────── CREATE / GET ──────────────────────────────
    async def create(
        self,
        *,
        pending_id: int,
        chat_id: int,
        message_id: int | None,
        is_owner: bool,
        share_id: int | None = None,
        share_member_id: int | None = None,
    ) -> ActionPendingMessage:
        row = ActionPendingMessage(
            pending_id=pending_id,
            chat_id=chat_id,
            message_id=message_id,
            is_owner=is_owner,
            share_id=share_id,
            share_member_id=share_member_id,
        )
        return await self.add(row)

    async def get(self, row_id: int) -> Optional[ActionPendingMessage]:
        return await self.session.get(ActionPendingMessage, row_id)

    async def get_with_relations(self, row_id: int) -> Optional[ActionPendingMessage]:
        q = (
            select(ActionPendingMessage)
            .where(ActionPendingMessage.id == row_id)
            .options(
                selectinload(ActionPendingMessage.pending),
                selectinload(ActionPendingMessage.share),
                selectinload(ActionPendingMessage.share_member)
                .selectinload(ShareMember.share)
                .selectinload(ShareLink.owner),
            )
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    # ────────────────────────────── LIST / QUERY ──────────────────────────────
    async def list_by_pending(self, pending_id: int) -> Sequence[ActionPendingMessage]:
        q = select(ActionPendingMessage).where(ActionPendingMessage.pending_id == pending_id)
        return (await self.session.execute(q)).scalars().all()

    async def list_by_pending_with_relations(self, pending_id: int) -> Sequence[ActionPendingMessage]:
        q = (
            select(ActionPendingMessage)
            .where(ActionPendingMessage.pending_id == pending_id)
            .options(
                selectinload(ActionPendingMessage.pending),
                selectinload(ActionPendingMessage.share),
                selectinload(ActionPendingMessage.share_member),
            )
        )
        return (await self.session.execute(q)).scalars().all()

    async def list_by_chat(self, chat_id: int) -> Sequence[ActionPendingMessage]:
        q = select(ActionPendingMessage).where(ActionPendingMessage.chat_id == chat_id)
        return (await self.session.execute(q)).scalars().all()

    async def list_by_share(self, share_id: int) -> Sequence[ActionPendingMessage]:
        q = select(ActionPendingMessage).where(ActionPendingMessage.share_id == share_id)
        return (await self.session.execute(q)).scalars().all()

    async def list_by_share_member(self, share_member_id: int) -> Sequence[ActionPendingMessage]:
        q = select(ActionPendingMessage).where(ActionPendingMessage.share_member_id == share_member_id)
        return (await self.session.execute(q)).scalars().all()

    async def list_distinct_chats_by_pending(self, pending_id: int) -> list[int]:
        """
        Возвращает уникальные chat_id, куда отправлялись сообщения этого pending'а.
        Удобно, если нужно пробежаться по чатам без привязки к message_id.
        """
        q = select(ActionPendingMessage.chat_id).where(ActionPendingMessage.pending_id == pending_id)
        rows = (await self.session.execute(q)).scalars().all()
        # порядок не важен; приводим к списку уникальных
        return list(dict.fromkeys(rows))

    # ────────────────────────────── UPDATE ────────────────────────────────────
    async def set_message_id(self, row_id: int, message_id: int | None) -> None:
        """
        Полезно, если сначала отправили сообщение (получили message_id) и хотим обновить запись.
        """
        await self.session.execute(
            update(ActionPendingMessage)
            .where(ActionPendingMessage.id == row_id)
            .values(message_id=message_id)
        )

    async def bulk_clear_message_ids(self, ids: Iterable[int]) -> None:
        """
        Массово обнуляет message_id (например, после удаления сообщений).
        """
        ids = list(ids)
        if not ids:
            return
        await self.session.execute(
            update(ActionPendingMessage)
            .where(ActionPendingMessage.id.in_(ids))
            .values(message_id=None)
        )

    # ────────────────────────────── DELETE / CLEANUP ──────────────────────────
    async def delete(self, row_id: int) -> None:
        await self.session.execute(delete(ActionPendingMessage).where(ActionPendingMessage.id == row_id))

    async def delete_by_pending(self, pending_id: int) -> None:
        await self.session.execute(delete(ActionPendingMessage).where(ActionPendingMessage.pending_id == pending_id))

    async def delete_by_chats(self, pending_id: int, chat_ids: Iterable[int]) -> None:
        chat_ids = list(chat_ids)
        if not chat_ids:
            return
        await self.session.execute(
            delete(ActionPendingMessage).where(
                ActionPendingMessage.pending_id == pending_id,
                ActionPendingMessage.chat_id.in_(chat_ids),
            )
        )