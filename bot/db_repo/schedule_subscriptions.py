# bot/db_repo/schedule_subscriptions.py
from __future__ import annotations
from typing import Optional, List, Sequence
from datetime import datetime, timezone

from sqlalchemy import select, update, delete, and_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from .models import (
    ScheduleSubscription,
    ScheduleShare,
    Schedule,
    Plant,
    User,
)


class ScheduleSubscriptionsRepo:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get(self, subscription_id: int) -> Optional[ScheduleSubscription]:
        return await self.session.get(ScheduleSubscription, subscription_id)

    async def get_for_user_and_schedule(self, subscriber_user_id: int, schedule_id: int) -> Optional[ScheduleSubscription]:
        q = select(ScheduleSubscription).where(
            and_(
                ScheduleSubscription.subscriber_user_id == subscriber_user_id,
                ScheduleSubscription.schedule_id == schedule_id,
            )
        )
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_by_schedule(self, schedule_id: int) -> Sequence[ScheduleSubscription]:
        q = (
            select(ScheduleSubscription)
            .where(ScheduleSubscription.schedule_id == schedule_id)
            .order_by(ScheduleSubscription.id.desc())
        )
        return (await self.session.execute(q)).scalars().all()

    async def list_by_user(self, subscriber_user_id: int) -> Sequence[ScheduleSubscription]:
        q = (
            select(ScheduleSubscription)
            .where(ScheduleSubscription.subscriber_user_id == subscriber_user_id)
            .order_by(ScheduleSubscription.id.desc())
        )
        return (await self.session.execute(q)).scalars().all()

    async def list_subscriber_tg_user_ids(self, schedule_id: int, *, include_muted: bool = False) -> List[int]:
        q = (
            select(User.tg_user_id)
            .select_from(ScheduleSubscription)
            .join(User, User.id == ScheduleSubscription.subscriber_user_id)
            .where(ScheduleSubscription.schedule_id == schedule_id)
        )
        if not include_muted:
            q = q.where(ScheduleSubscription.muted.is_(False))
        return list((await self.session.execute(q)).scalars().all())

    async def create_direct(
        self,
        *,
        schedule_id: int,
        subscriber_user_id: int,
        can_complete: bool = True,
        muted: bool = False,
    ) -> ScheduleSubscription:
        sub = ScheduleSubscription(
            schedule_id=schedule_id,
            subscriber_user_id=subscriber_user_id,
            can_complete=can_complete,
            muted=muted,
        )
        self.session.add(sub)
        await self.session.flush()
        return sub

    async def subscribe_with_code(self, *, subscriber_user_id: int, code: str) -> ScheduleSubscription:
        share_q = select(ScheduleShare).where(ScheduleShare.code == code)
        share = (await self.session.execute(share_q)).scalar_one_or_none()
        if share is None:
            raise ValueError("Код не найден")

        if not share.is_active:
            raise ValueError("Код деактивирован владельцем")
        if share.expires_at_utc is not None:
            now_utc = datetime.now(timezone.utc)
            if share.expires_at_utc <= now_utc:
                raise ValueError("Срок действия кода истёк")

        if share.owner_user_id == subscriber_user_id:
            raise ValueError("Нельзя подписаться на собственное расписание")

        sub = ScheduleSubscription(
            schedule_id=share.schedule_id,
            subscriber_user_id=subscriber_user_id,
            can_complete=share.allow_complete_by_subscribers,
            muted=False,
        )
        self.session.add(sub)
        try:
            await self.session.flush()
            return sub
        except IntegrityError:
            await self.session.rollback()
            existing = await self.get_for_user_and_schedule(subscriber_user_id, share.schedule_id)
            if existing:
                return existing
            raise

    async def unsubscribe(self, *, schedule_id: int, subscriber_user_id: int) -> None:
        await self.session.execute(
            delete(ScheduleSubscription).where(
                and_(
                    ScheduleSubscription.schedule_id == schedule_id,
                    ScheduleSubscription.subscriber_user_id == subscriber_user_id,
                )
            )
        )

    async def delete(self, subscription_id: int) -> None:
        await self.session.execute(
            delete(ScheduleSubscription).where(ScheduleSubscription.id == subscription_id)
        )

    async def set_muted(self, *, schedule_id: int, subscriber_user_id: int, muted: bool) -> None:
        await self.session.execute(
            update(ScheduleSubscription)
            .where(
                and_(
                    ScheduleSubscription.schedule_id == schedule_id,
                    ScheduleSubscription.subscriber_user_id == subscriber_user_id,
                )
            )
            .values(muted=muted)
        )

    async def set_can_complete(self, *, schedule_id: int, subscriber_user_id: int, can_complete: bool) -> None:
        await self.session.execute(
            update(ScheduleSubscription)
            .where(
                and_(
                    ScheduleSubscription.schedule_id == schedule_id,
                    ScheduleSubscription.subscriber_user_id == subscriber_user_id,
                )
            )
            .values(can_complete=can_complete)
        )

    async def delete_all_for_schedule(self, schedule_id: int) -> None:
        await self.session.execute(
            delete(ScheduleSubscription).where(ScheduleSubscription.schedule_id == schedule_id)
        )

    async def can_user_complete(self, *, schedule_id: int, caller_user_id: int) -> bool:
        owner_q = (
            select(Plant.user_id)
            .select_from(Schedule)
            .join(Plant, Plant.id == Schedule.plant_id)
            .where(Schedule.id == schedule_id)
        )
        owner_id = (await self.session.execute(owner_q)).scalar_one_or_none()
        if owner_id is not None and owner_id == caller_user_id:
            return True

        sub_q = select(ScheduleSubscription.id).where(
            and_(
                ScheduleSubscription.schedule_id == schedule_id,
                ScheduleSubscription.subscriber_user_id == caller_user_id,
                ScheduleSubscription.can_complete.is_(True),
            )
        )
        return (await self.session.execute(sub_q)).scalar_one_or_none() is not None