# bot/db_repo/unit_of_work.py
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from .action_logs import ActionLogsRepo
from .base import AsyncSessionLocal
from .share_link_schedules import ShareLinkSchedulesRepo
from .share_links import ShareLinksRepo
from .share_members import ShareMembersRepo

from .users import UsersRepo
from .plants import PlantsRepo
from .schedules import SchedulesRepo
from .species import SpeciesRepo
from .jobs import JobsRepo


class UnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UsersRepo(session)
        self.plants = PlantsRepo(session)
        self.schedules = SchedulesRepo(session)
        self.species = SpeciesRepo(session)
        self.jobs = JobsRepo(session)
        self.action_logs = ActionLogsRepo(session)
        self.share_links = ShareLinksRepo(session)
        self.share_link_schedules = ShareLinkSchedulesRepo(session)
        self.share_members = ShareMembersRepo(session)


    async def commit(self) -> None:
        await self.session.commit()

    async def rollback(self) -> None:
        await self.session.rollback()


@asynccontextmanager
async def new_uow() -> AsyncIterator[UnitOfWork]:
    async with AsyncSessionLocal() as session:
        uow = UnitOfWork(session)
        try:
            yield uow
            await uow.commit()
        except Exception:
            await uow.rollback()
            raise