# bot/db_repo/schedule_shares.py
from __future__ import annotations
from typing import Optional, List, Sequence
from datetime import datetime, timedelta
import string
import secrets

from sqlalchemy import select, update, delete
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.exc import IntegrityError

from .models import ScheduleShare


def _generate_human_code(length: int = 8) -> str:
    """
    Генерит человекочитаемый код: A-Z + 0-9, без похожих символов.
    Пример: 'K7F9A3Q2'
    """
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # без I, O, 0, 1
    return "".join(secrets.choice(alphabet) for _ in range(length))


class ScheduleShareRepo:
    """
    Репозиторий кодов расшаривания расписаний.
    """

    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    # ---------- READ ----------

    async def get(self, share_id: int) -> Optional[ScheduleShare]:
        return await self.session.get(ScheduleShare, share_id)

    async def get_by_code(self, code: str) -> Optional[ScheduleShare]:
        q = select(ScheduleShare).where(ScheduleShare.code == code)
        return (await self.session.execute(q)).scalar_one_or_none()

    async def list_by_owner(
        self, owner_user_id: int, *, only_active: Optional[bool] = None
    ) -> Sequence[ScheduleShare]:
        q = select(ScheduleShare).where(ScheduleShare.owner_user_id == owner_user_id)
        if only_active is True:
            q = q.where(ScheduleShare.is_active.is_(True))
        elif only_active is False:
            q = q.where(ScheduleShare.is_active.is_(False))
        q = q.order_by(ScheduleShare.id.desc())
        return (await self.session.execute(q)).scalars().all()

    # ---------- HELPERS ----------

    @staticmethod
    def is_share_effectively_active(share: ScheduleShare, now_utc: Optional[datetime] = None) -> bool:
        if not share.is_active:
            return False
        if share.expires_at_utc is None:
            return True
        if now_utc is None:
            now_utc = datetime.utcnow().replace(tzinfo=share.expires_at_utc.tzinfo)  # обычно UTC-aware
        return share.expires_at_utc > now_utc

    # ---------- WRITE ----------

    async def create_share(
        self,
        *,
        owner_user_id: int,
        schedule_id: int,
        note: Optional[str] = None,
        allow_complete_by_subscribers: bool = True,
        expires_at_utc: Optional[datetime] = None,
        code: Optional[str] = None,
        code_len: int = 8,
        max_retries: int = 5,
    ) -> ScheduleShare:
        """
        Создать объект расшаривания. Если code не задан — генерируем.
        Защищаемся от гонки по уникальному коду ретраями.
        """
        last_err: Optional[Exception] = None
        for _ in range(max_retries):
            use_code = code or _generate_human_code(code_len)
            share = ScheduleShare(
                owner_user_id=owner_user_id,
                schedule_id=schedule_id,
                code=use_code,
                note=note,
                is_active=True,
                allow_complete_by_subscribers=allow_complete_by_subscribers,
                expires_at_utc=expires_at_utc,
            )
            self.session.add(share)
            try:
                await self.session.flush()  # получим id и проверим уникальность кода
                return share
            except IntegrityError as e:
                await self.session.rollback()
                last_err = e
                # пробуем ещё раз с новым кодом (если код не был задан явно)
                if code:
                    # код вручную задан и уже занят — сразу отдаём ошибку
                    raise
                continue
        # если так и не удалось
        if last_err:
            raise last_err
        raise RuntimeError("Failed to create ScheduleShare for unknown reason")

    async def set_active(self, share_id: int, is_active: bool) -> None:
        await self.session.execute(
            update(ScheduleShare).where(ScheduleShare.id == share_id).values(is_active=is_active)
        )

    async def revoke(self, share_id: int) -> None:
        """Снимает активность кода (новые подписки по нему будут запрещены)."""
        await self.set_active(share_id, False)

    async def activate(self, share_id: int) -> None:
        await self.set_active(share_id, True)

    async def update_note(self, share_id: int, note: Optional[str]) -> None:
        await self.session.execute(
            update(ScheduleShare).where(ScheduleShare.id == share_id).values(note=note)
        )

    async def update_expiry(self, share_id: int, expires_at_utc: Optional[datetime]) -> None:
        await self.session.execute(
            update(ScheduleShare).where(ScheduleShare.id == share_id).values(expires_at_utc=expires_at_utc)
        )

    async def update_allow_complete(self, share_id: int, allow: bool) -> None:
        await self.session.execute(
            update(ScheduleShare)
            .where(ScheduleShare.id == share_id)
            .values(allow_complete_by_subscribers=allow)
        )

    async def delete(self, share_id: int) -> None:
        await self.session.execute(delete(ScheduleShare).where(ScheduleShare.id == share_id))