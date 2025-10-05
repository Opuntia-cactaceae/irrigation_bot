# bot/db_repo/unit_of_work.py
from contextlib import asynccontextmanager
from typing import AsyncIterator

from sqlalchemy.ext.asyncio import AsyncSession

from .action_logs import ActionLogsRepo
from .base import AsyncSessionLocal
from .schedule_shares import ScheduleShareRepo
from .schedule_subscriptions import ScheduleSubscriptionsRepo

from .users import UsersRepo
from .plants import PlantsRepo
from .schedules import SchedulesRepo
from .events import EventsRepo
from .species import SpeciesRepo
from .jobs import JobsRepo


class UnitOfWork:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session
        self.users = UsersRepo(session)
        self.plants = PlantsRepo(session)
        self.schedules = SchedulesRepo(session)
        self.events = EventsRepo(session)
        self.species = SpeciesRepo(session)
        self.jobs = JobsRepo(session)
        self.logs = ActionLogsRepo(session)
        self.logs = ScheduleShareRepo(session)
        self.logs = ScheduleSubscriptionsRepo(session)

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