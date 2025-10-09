# bot/services/calendar_feed.py
from __future__ import annotations
from dataclasses import dataclass
from datetime import datetime, date, time, timedelta
from math import ceil
from typing import Optional, Literal, Dict, List

import pytz

from bot.db_repo.unit_of_work import new_uow
from bot.db_repo.models import User, Plant, Schedule, ActionType, ScheduleType, ActionSource, ActionStatus
from .rules import next_by_interval, next_by_weekly

Mode = Literal["upc", "hist"]

UPC_MAX_DAYS = 90
HIST_MAX_DAYS = 180


@dataclass
class FeedItem:
    dt_utc: datetime
    dt_local: datetime
    plant_id: int
    plant_name: str
    action: ActionType
    schedule_id: int


@dataclass
class FeedDay:
    date_local: date
    items: List[FeedItem]


@dataclass
class FeedPage:
    page: int
    pages: int
    days: List[FeedDay]


def _safe_tz(name: Optional[str]) -> pytz.BaseTzInfo:
    try:
        return pytz.timezone(name or "UTC")
    except Exception:
        return pytz.UTC


def _localize_day_bounds(tz: pytz.BaseTzInfo, d: date) -> tuple[datetime, datetime]:
    start_naive = datetime.combine(d, time.min)
    end_naive = datetime.combine(d, time.max)
    start_local = tz.localize(start_naive)
    end_local = tz.localize(end_naive)
    return start_local, end_local


async def get_feed(
    user_tg_id: int,
    action: Optional[ActionType],
    plant_id: Optional[int],
    mode: Mode,
    page: int,
    days_per_page: int,
) -> FeedPage:
    async with new_uow() as uow:
        user: User = await uow.users.get(user_tg_id)
        try:
            plants: List[Plant] = await uow.plants.list_by_user_with_relations(user.id)
        except AttributeError:
            plants: List[Plant] = await uow.plants.list_by_user(user.id)

        if plant_id:
            plants = [p for p in plants if p.id == plant_id]

        tz = _safe_tz(getattr(user, "tz", None))
        today_local = datetime.now(tz).date()

        if mode == "upc":
            page = max(1, page)
            start_local_day = today_local + timedelta(days=(page - 1) * days_per_page)
            end_local_day = start_local_day + timedelta(days=days_per_page - 1)
            max_days = UPC_MAX_DAYS
        else:
            page = max(1, page)
            end_local_day = today_local - timedelta(days=(page - 1) * days_per_page + 1)
            start_local_day = end_local_day - timedelta(days=days_per_page - 1)
            max_days = HIST_MAX_DAYS

        total_pages = max(1, ceil(max_days / days_per_page))

        start_local_dt, end_local_dt = _localize_day_bounds(tz, start_local_day)
        if end_local_day != start_local_day:
            _, end_local_dt = _localize_day_bounds(tz, end_local_day)

        start_utc = start_local_dt.astimezone(pytz.UTC)
        end_utc = end_local_dt.astimezone(pytz.UTC)

        items: List[FeedItem] = []

        for p in plants:
            p_schedules: List[Schedule] = list(getattr(p, "schedules", []) or [])
            schedules: List[Schedule] = [
                s for s in p_schedules
                if getattr(s, "active", True) and (action is None or s.action == action)
            ]
            if not schedules:
                continue

            last_by_schedule: Dict[int, tuple[Optional[datetime], Optional[ActionSource]]] = {}
            for s in schedules:
                logs = await uow.action_logs.list_by_schedule(
                    s.id, limit=10, offset=0, with_relations=False
                )
                last_dt, last_src = None, None
                for lg in logs:
                    if lg.status == ActionStatus.DONE:
                        last_dt, last_src = lg.done_at_utc, lg.source
                        break
                last_by_schedule[s.id] = (last_dt, last_src)

            for s in schedules:
                base_now: datetime = start_utc - timedelta(seconds=1)
                cursor: Optional[datetime] = None
                last_dt_utc, last_src = last_by_schedule.get(s.id, (None, None))
                plant_name = getattr(p, "name", None) or f"#{getattr(p, 'id', 0)}"

                for _ in range(days_per_page * 8):
                    last_anchor = last_dt_utc if cursor is None else cursor

                    if s.type == ScheduleType.INTERVAL:
                        nxt = next_by_interval(
                            last_anchor,
                            s.interval_days,
                            s.local_time,
                            getattr(user, "tz", "UTC"),
                            base_now,
                        )
                    else:
                        nxt = next_by_weekly(
                            last_done_utc=last_anchor,
                            last_done_source=last_src,
                            weekly_mask=s.weekly_mask,
                            local_t=s.local_time,
                            tz_name=getattr(user, "tz", "UTC"),
                            now_utc=base_now,
                        )

                    if nxt > end_utc:
                        break

                    d_loc = nxt.astimezone(tz).date()
                    if start_local_day <= d_loc <= end_local_day:
                        items.append(
                            FeedItem(
                                dt_utc=nxt,
                                dt_local=nxt.astimezone(tz),
                                plant_id=p.id,
                                plant_name=plant_name,
                                action=s.action,
                                schedule_id=s.id,
                            )
                        )

                    cursor = nxt
                    base_now = nxt
                    if s.type != ScheduleType.INTERVAL:
                        last_src = ActionSource.SCHEDULE

        by_day: Dict[date, List[FeedItem]] = {}
        for it in items:
            d = it.dt_local.date()
            by_day.setdefault(d, []).append(it)

        days: List[FeedDay] = []
        cur = start_local_day
        while cur <= end_local_day:
            day_items = sorted(by_day.get(cur, []), key=lambda x: x.dt_local)
            if day_items:
                days.append(FeedDay(date_local=cur, items=day_items))
            cur += timedelta(days=1)

        return FeedPage(page=page, pages=total_pages, days=days)